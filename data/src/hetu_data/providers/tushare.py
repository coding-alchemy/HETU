"""已核Tushare合同的薄适配；原价与除息参考、观察与公开时间分别保存。

未证PIT不生成已核历史公开资格；无自动分页/重试，真实请求须有预算守卫。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import ssl
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PRODUCTS = {
    "ts_a_basic": ("stock_basic", "securities"),
    "ts_trade_calendar": ("trade_cal", "calendar"),
    "ts_trade_calendar_sz": ("trade_cal", "calendar"),
    "ts_bak_basic": ("bak_basic", "security_observations"),
    "ts_bse_mapping": ("bse_mapping", "security_observations"),
    "ts_a_daily": ("daily", "daily_bars"),
    "ts_adj_factor": ("adj_factor", "adjustment_factors"),
    "ts_adj_factor_day": ("adj_factor", "adjustment_factors"),
    "ts_a_market_cap": ("daily_basic", "market_cap"),
    "ts_action_versions": ("dividend", "action_versions"),
    "ts_a_actions": ("dividend", "corporate_actions"),
    "ts_suspend_events": ("suspend_d", "security_events"),
    "ts_suspend_day": ("suspend_d", "security_events"),
    "ts_st_day": ("stock_st", "security_events"),
    "ts_price_limits": ("stk_limit", "price_limits"),
    "ts_sw_daily": ("sw_daily", "index_bars"),
}
FIELDS = {
    "stock_basic": "ts_code,symbol,name,fullname,exchange,market,list_status,list_date,delist_date",
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "bak_basic": "trade_date,ts_code,name,total_share,float_share",
    "bse_mapping": "name,o_code,n_code,list_date",
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "daily_basic": "ts_code,trade_date,close,total_share,float_share,free_share,total_mv,circ_mv",
    "dividend": (
        "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div_tax,record_date,"
        "ex_date,pay_date,div_listdate,imp_ann_date,base_date,base_share"
    ),
    "suspend_d": "ts_code,trade_date,suspend_timing,suspend_type",
    "stock_st": "ts_code,name,trade_date,type,type_name",
    "stk_limit": "ts_code,trade_date,pre_close,up_limit,down_limit,asset_type,exchange",
    "sw_daily": "ts_code,trade_date,name,open,high,low,close,vol,amount,pe,pb,total_mv,float_mv",
}
ROW_LIMIT = {
    "stock_basic": 6000,
    "bak_basic": 7000,
    "bse_mapping": 1000,
    "daily": 6000,
    "daily_basic": 6000,
    "dividend": 2000,
    "suspend_d": 5000,
    "stock_st": 1000,
    "stk_limit": 5800,
    "sw_daily": 4000,
}

REQUIRED_FIELDS = {
    "stock_basic": {"ts_code", "name", "list_status", "list_date", "delist_date"},
    "trade_cal": {"cal_date", "is_open"},
    "bak_basic": {"ts_code", "trade_date", "name", "total_share"},
    "bse_mapping": set(FIELDS["bse_mapping"].split(",")),
    "daily": set(FIELDS["daily"].split(",")),
    "adj_factor": {"ts_code", "trade_date", "adj_factor"},
    "daily_basic": {"ts_code", "trade_date", "close", "total_share", "total_mv"},
    "dividend": {
        "ts_code",
        "end_date",
        "ann_date",
        "div_proc",
        "stk_div",
        "cash_div_tax",
        "record_date",
        "ex_date",
        "pay_date",
        "div_listdate",
        "imp_ann_date",
    },
    "suspend_d": {"ts_code", "trade_date", "suspend_type", "suspend_timing"},
    "stock_st": set(FIELDS["stock_st"].split(",")),
    "stk_limit": {"ts_code", "trade_date", "pre_close", "up_limit", "down_limit"},
    "sw_daily": {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"},
}


class IncompleteResponse(ValueError):
    """返回不完整或查询语义不足，不登记整个期间覆盖。"""


MARKET_DAY_PRODUCTS = frozenset({"ts_suspend_day", "ts_adj_factor_day", "ts_st_day"})


def _day(value):
    if value is None or pd.isna(value) or str(value) in {"", "None", "nan"}:
        return None
    return date.fromisoformat(str(value)).isoformat()


def calls_for(product_id, codes, start, end):
    api = PRODUCTS[product_id][0]
    if product_id in MARKET_DAY_PRODUCTS:
        if codes or start != end:
            raise ValueError("market cross-section requires a single day and no code filter")
        return [(api, {"trade_date": start.strftime("%Y%m%d")})]
    if api == "bse_mapping":
        if codes:
            raise ValueError("BSE mapping query does not accept a code filter")
        return [(api, {})]
    if api == "trade_cal":
        exchange = "SZSE" if product_id == "ts_trade_calendar_sz" else "SSE"
        params = {"exchange": exchange, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
        return [(api, params)]
    if api == "bak_basic":
        return [
            (api, {"trade_date": (start + timedelta(days=i)).strftime("%Y%m%d")}) for i in range((end - start).days + 1)
        ]
    calls = []
    for code in codes:
        if api == "stock_basic":
            calls.extend((api, {"ts_code": code, "list_status": status}) for status in ("L", "D", "P"))
        elif api == "dividend":
            calls.append((api, {"ts_code": code}))
        else:
            calls.append(
                (api, {"ts_code": code, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")})
            )
    return calls


def directory_calls():
    """显式目录范围；仅供独立获批入口使用，不改变逐码fetch合同。"""
    return [
        ("stock_basic", {"exchange": exchange, "list_status": status})
        for exchange in ("SSE", "SZSE", "BSE")
        for status in ("L", "D", "P")
    ]


class TushareProvider:
    name = "tushare"

    def __init__(
        self,
        *,
        client: object | None = None,
        before_request: Callable[[dict], None] | None = None,
        on_usage: Callable[[dict], None] | None = None,
    ) -> None:
        self.client = client
        # execute_update仅在同一未完成分片显式续接时提供已验原件；不再发送/计量成功上游。
        self.resume_responses = []
        self.before_request, self.on_usage = before_request, on_usage

    def _query(self, api, params, fields):
        if self.client is not None:
            return self.client.query(api, params, fields)
        # 默认注册本身不授权联网，只有受控执行入口注入守卫后可调用。
        if self.before_request is None:
            raise PermissionError("live Tushare requests require an approved budget guard")
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            credential = Path(__file__).resolve().parents[4] / ".hetu/credentials/tushare.env"
            if credential.is_symlink() or credential.stat().st_mode & 0o077:
                raise PermissionError("credential file must be private")
            for line in credential.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "TUSHARE_TOKEN":
                    token = value.strip().strip("\"'")
        if not token:
            raise PermissionError("TUSHARE_TOKEN missing")
        conn = http.client.HTTPSConnection("api.tushare.pro", context=ssl.create_default_context(), timeout=30)
        try:
            conn.request(
                "POST",
                "/",
                json.dumps(dict(api_name=api, params=params, fields=fields, token=token)).encode(),
                {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = response.read()
            if token.encode() in body:
                raise PermissionError("credential echoed by upstream; response not stored")
            if response.status != 200:
                raise ConnectionError(f"Tushare HTTP status {response.status}; no automatic retry or redirect")
            return body
        finally:
            conn.close()

    def decode_response(self, body: bytes, *, api: str, params: Mapping, obtained_at: str) -> pd.DataFrame:
        payload = json.loads(body)
        if payload.get("code") != 0:
            raise IncompleteResponse(f"Tushare provider code {payload.get('code')}; not successful coverage")
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("items"), list)
            or not isinstance(data.get("fields"), list)
        ):
            raise IncompleteResponse("missing provider table")
        if api not in REQUIRED_FIELDS or not REQUIRED_FIELDS[api].issubset(data["fields"]):
            raise IncompleteResponse("required response fields missing; not successful empty coverage")
        if payload.get("has_more") is True or data.get("has_more") is True:
            raise IncompleteResponse("has_more without a proven pagination contract")
        if any(len(row) != len(data["fields"]) for row in data["items"]):
            raise IncompleteResponse("provider row width mismatch")
        if api in ROW_LIMIT and len(data["items"]) >= ROW_LIMIT[api]:
            raise IncompleteResponse("row limit reached; complete interval not proven")
        raw = pd.DataFrame(data["items"], columns=data["fields"])
        if api in {"suspend_d", "adj_factor", "stock_st"} and "trade_date" in params and len(raw):
            if not raw.trade_date.map(_day).eq(_day(params["trade_date"])).all():
                raise IncompleteResponse("market response outside requested day")
        if api == "suspend_d" and len(raw):
            if not raw.suspend_type.isin(("S", "R")).all():
                raise IncompleteResponse("unrecognized suspension event type")
            if "start_date" in params and "end_date" in params and not raw.trade_date.map(_day).between(
                _day(params["start_date"]), _day(params["end_date"]),
            ).all():
                raise IncompleteResponse("suspension response outside requested period")
        if api == "bse_mapping" and len(raw):
            for field in ("o_code", "n_code"):
                if not raw[field].astype(str).str.endswith(".BJ").all() or raw[field].duplicated().any():
                    raise IncompleteResponse("invalid or ambiguous BSE mapping")
            if raw.o_code.eq(raw.n_code).any() or raw.list_date.map(_day).isna().any():
                raise IncompleteResponse("invalid BSE listing observation")
        if api == "trade_cal" and "start_date" in params and "end_date" in params:
            wanted = {d.date().isoformat() for d in pd.date_range(params["start_date"], params["end_date"])}
            if len(raw) != len(wanted) or {_day(d) for d in raw.cal_date} != wanted:
                raise IncompleteResponse("calendar query does not contain every natural day")
            if params.get("exchange") == "SZSE":
                if "exchange" not in raw or not raw.exchange.eq("SZSE").all():
                    raise IncompleteResponse("calendar response for another exchange")
                if not raw.is_open.astype(str).isin(("0", "1")).all():
                    raise IncompleteResponse("invalid calendar open marker")
        digest = hashlib.sha256(body).hexdigest()
        raw["_response_row"] = range(len(raw))
        raw["_response_sha256"] = digest
        raw["_obtained_at"] = obtained_at
        raw.attrs["provider_responses"] = [
            {
                "body": body.decode("utf-8"),
                "api": api,
                "params": dict(params),
                "obtained_at": obtained_at,
                "sha256": digest,
            }
        ]
        # 仅证明本次查询的口径，不把无日期全史接口升级为实施日完整覆盖。
        raw.attrs["query_evidence"] = []
        if "ts_code" in params:
            raw.attrs["query_evidence"] = [
                {
                    "code": params["ts_code"],
                    "basis": "provider_observation",
                    "api": api,
                    "params": dict(params),
                    "obtained_at": obtained_at,
                }
            ]
        if "ts_code" in params and len(raw) and "ts_code" in raw and not raw.ts_code.eq(params["ts_code"]).all():
            raise IncompleteResponse("provider returned another object")
        if api == "daily" and len(raw) and "start_date" in params and "end_date" in params:
            days = raw.trade_date.map(_day)
            if not days.between(_day(params["start_date"]), _day(params["end_date"])).all():
                raise IncompleteResponse("daily response outside requested period")
        if api == "stock_basic" and len(raw):
            for field in ("exchange", "list_status"):
                if field in params and (field not in raw or not raw[field].eq(params[field]).all()):
                    raise IncompleteResponse("directory returned another exchange or listing status")
        return raw

    def fetch(self, product_id: str, codes: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
        frames, responses, evidence = [], [], []
        for api, params in calls_for(product_id, codes, start, end):
            event = dict(
                product_id=product_id,
                api=api,
                params=params,
                category="product",
                sdk_calls=0,
                upstream_product_requests=1,
                network_attempts=1,
                auth_calls=0,
                retries=0,
            )
            cached = next((r for r in self.resume_responses if r["api"] == api and r["params"] == params), None)
            if self.before_request and cached is None:
                self.before_request(event)
            observed = cached["obtained_at"] if cached else datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            body = None
            try:
                body = cached["body"].encode("utf-8") if cached else self._query(api, params, FIELDS[api])
                raw = self.decode_response(body, api=api, params=params, obtained_at=observed)
            except Exception:
                if self.on_usage and cached is None:
                    failure = {**event, "rows": None, "quota_bytes": None, "failed": True}
                    if body is not None:
                        failure["raw_response"] = dict(
                            body=body.decode("utf-8"), api=api, params=params, obtained_at=observed
                        )
                    self.on_usage(failure)
                raise
            if self.on_usage and cached is None:
                self.on_usage(
                    {
                        **event,
                        "rows": len(raw),
                        "quota_bytes": None,
                        "failed": False,
                        "raw_response": raw.attrs["provider_responses"][0],
                    }
                )
            responses.extend(raw.attrs["provider_responses"])
            evidence.extend(raw.attrs["query_evidence"])
            frames.append(raw)
        if not frames:
            raise ValueError("objects required for selected product")
        result = pd.concat(frames, ignore_index=True)
        result.attrs.update(provider_responses=responses, query_evidence=evidence)
        return result

    def standardize(self, product_id: str, raw: pd.DataFrame, product: Mapping) -> dict[str, pd.DataFrame]:
        api, table = PRODUCTS[product_id]
        rows = []
        for record in raw.to_dict("records"):
            observed = record["_obtained_at"]
            row = {k: v for k, v in record.items() if not k.startswith("_")}
            row["code"] = record.get("ts_code")
            row["revision_id"] = f"{record['_response_sha256']}:{record['_response_row']}"
            row["observed_at"] = observed
            if "trade_date" in record:
                row["date"] = _day(record["trade_date"])
            if api == "stock_basic":
                row.update(
                    ipo_date=_day(record.get("list_date")),
                    out_date=_day(record.get("delist_date")),
                    market=str(record["ts_code"]).split(".")[-1],
                    security_type="b_share" if str(record["ts_code"]).startswith(("900", "200")) else "stock",
                    status=record.get("list_status"),
                )
            elif api == "trade_cal":
                row.update(date=_day(record["cal_date"]), is_trading_day=int(record["is_open"]) == 1)
            elif api == "daily":
                row.update({f"{k}_raw": float(record[k]) for k in ("open", "high", "low", "close")})
                row.update(
                    provider_pre_close=record.get("pre_close"),
                    volume_shares=float(record["vol"]) * 100,
                    amount_cny=float(record["amount"]) * 1000,
                    close_price_return=None,
                    close_total_return=None,
                )
            elif api == "daily_basic":
                row.update(
                    market_cap_cny=float(record["total_mv"]) * 10000,
                    available_at=observed,
                    total_shares=float(record["total_share"]) * 10000,
                )
            elif api == "bak_basic":
                row.update(total_shares=float(record["total_share"]) * 100000000)
            elif api == "bse_mapping":
                row.update(code=record["n_code"], old_code=record["o_code"], date=_day(record["list_date"]))
            elif api == "dividend":
                for name in ("record_date", "ex_date", "pay_date"):
                    row[name] = _day(record.get(name))
                row.update(
                    stock_listing_date=_day(record.get("div_listdate")),
                    available_at=observed,
                    published_at=None,
                    supersedes="",
                )
                if product_id == "ts_a_actions":
                    if record.get("div_proc") != "实施" or not row["ex_date"]:
                        continue
                    row["terms_raw"] = json.dumps(
                        {k: v for k, v in record.items() if not k.startswith("_")}, ensure_ascii=False
                    )
                    for kind, value in (
                        ("cash_dividend", record.get("cash_div_tax")),
                        ("bonus_split", record.get("stk_div")),
                    ):
                        if value is not None and not pd.isna(value) and float(value) != 0:
                            rows.append(
                                {
                                    **row,
                                    "action_type": kind,
                                    "m": 1 + float(value) if kind == "bonus_split" else 1,
                                    "d": float(value) if kind == "cash_dividend" else 0,
                                }
                            )
                    continue
            elif api == "suspend_d":
                row.update(event_type=record.get("suspend_type"))
            elif api == "stock_st":
                row.update(event_type=record["type"])
            elif api == "stk_limit":
                row.update(reference_price=record.get("pre_close"))
            elif api == "sw_daily":
                row.update(
                    close=float(record["close"]),
                    amount_cny=float(record["amount"]) * 10000,
                    volume_shares=float(record["vol"]) * 10000,
                    return_basis="price",
                )
            rows.append(row)
        if rows:
            return {table: pd.DataFrame(rows)}
        from hetu_data.store import TABLE_SCHEMAS

        return {table: pd.DataFrame(columns=TABLE_SCHEMAS[table]["columns"])}
