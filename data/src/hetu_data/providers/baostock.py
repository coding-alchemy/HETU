"""BaoStock 适配器：原始返回保留原形，标准化映射在 provider 内完成。

实测要点（2026-10-14，evidence 见 .hetu/quant/qualification/）：
- query_stock_basic().get_data() 内部使用 pandas 已移除的 DataFrame.append，
  本适配器统一用 rs.next()/get_row_data() 逐行收集。
- volume 单位为股，amount 单位为元人民币；adjustflag='3' 为不复权原价。
- 分红 query_dividend_data 提供税前每股现金、每股送转股数及各公告/实施日期。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import time
import zlib
from collections.abc import Mapping
from datetime import date, datetime
from threading import RLock
from zoneinfo import ZoneInfo

import pandas as pd

DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
_SDK_LOCK = RLock()


class _StatusSocket:
    """Socket boundary for the SDK decoder, with bounded reads and original bytes."""

    def __init__(self, conn):
        self.conn = conn
        self.received = bytearray()
        self.wire_limit = 1024 * 1024

    def send(self, payload):
        self.received.clear()
        self.deadline = time.monotonic() + 30
        self.conn.settimeout(30)
        self.conn.sendall(payload)

    def recv(self, length):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("BaoStock response deadline exceeded")
        self.conn.settimeout(left)
        chunk = self.conn.recv(length)
        if not chunk:
            raise ConnectionError("BaoStock socket closed before complete response")
        self.received.extend(chunk)
        if len(self.received) > self.wire_limit:
            raise ValueError("BaoStock status response exceeds wire size bound")
        return chunk

    def close(self):
        self.conn.close()


def normalize_code(code: str) -> str:
    """sh.600519 / 600519 -> 600519.SH。"""
    code = code.strip()
    if "." in code:
        market, num = code.split(".", 1)
        return f"{num}.{market.upper()}"
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def dedupe_dividend_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """同一除息事件的重复记录按 (code, 除息日, 送转要素) 保留首见。

    实测（2026-10-14，002043.SZ 除息日 2026-06-01）：同一事件在同一年度
    查询内返回现金两个字段版本（0.132／0.18），送转要素一致。价格收益
    口径只消费送转因子，现金取值不影响；本去重只在 standardize 调用，
    raw 层逐行保留冲突原文。送转要素不同的同键记录不去重，
    交由标准化唯一性门拒绝。
    """
    if not len(frame):
        return frame
    return frame.drop_duplicates(
        subset=["code", "dividOperateDate", "dividStocksPs", "dividReserveToStockPs"],
        keep="first",
    )


class BaoStockProvider:
    name = "baostock"
    metered_products = frozenset({"cn_a_status", "cn_a_status_day"})

    def __init__(self) -> None:
        self._bs = None
        self._status_socket = None
        self.before_request = None
        self.on_usage = None
        self.resume_responses = []

    def _session(self):
        if self._bs is None:
            import baostock as bs

            login = bs.login()
            if login.error_code != "0":
                raise ConnectionError(f"baostock login failed: {login.error_msg}")
            self._bs = bs
        return self._bs

    def close(self) -> None:
        with _SDK_LOCK:
            self._close_status_socket()
            if self._bs is not None:
                self._bs.logout()
                self._bs = None

    def _close_status_socket(self):
        if self._status_socket is not None:
            # Close the local connection; no unmetered SDK logout message.
            self._status_socket.close()
            self._status_socket = None

    def _fetch_status_metered(self, product_id, codes, start, end):
        """Use one observable BaoStock 0.9.4 status query."""
        from importlib.metadata import version

        import baostock as bs
        from baostock.common import contants, context
        from baostock.util import socketutil

        if version("baostock") != "0.9.4" or self._bs is not None or getattr(context, "apiKey", "0") != "0":
            raise ValueError("metered BaoStock status requires the locked free SDK")
        whole_day = product_id == "cn_a_status_day"
        if whole_day:
            if codes or start != end:
                raise ValueError("metered BaoStock whole-day status requires one day without object parameters")
            query = bs.query_daily_history_k_AStock
            api = "query_daily_history_k_AStock"
            params = {"date": str(start)}
            response_kind = contants.MESSAGE_TYPE_GETKDaily_D_AStock_RESPONSE
            wire_limit = 4 * 1024 * 1024
        else:
            if len(codes) != 1 or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", codes[0]) or not 0 <= (end - start).days < 1827:
                raise ValueError("metered BaoStock single-code status requires one SH/SZ code and at most 1827 days")
            code = codes[0]
            sdk_code = f"{code[-2:].lower()}.{code[:6]}"
            query = bs.query_history_k_data_plus
            api = "query_history_k_data_plus"
            params = dict(code=sdk_code, fields=DAILY_FIELDS, start_date=str(start), end_date=str(end),
                          frequency="d", adjustflag="3")
            response_kind = contants.MESSAGE_TYPE_GETKDATAPLUS_RESPONSE
            wire_limit = 1024 * 1024
        cached = None
        if self.resume_responses:
            if len(self.resume_responses) != 1:
                raise ValueError("expected one saved BaoStock response")
            cached = self.resume_responses[0]
            if cached["api"] != api or cached["params"] != params:
                raise ValueError("saved BaoStock response does not match the requested window")
            digest = hashlib.sha256(cached["body"].encode()).hexdigest()
            if digest != cached["sha256"]:
                raise ValueError("saved BaoStock response hash mismatch")
            if datetime.fromisoformat(cached["obtained_at"]).utcoffset() is None:
                raise ValueError("saved BaoStock response lacks an aware acquisition time")
            cached = {**cached, "source": "baostock"}
        absent = object()
        prior = {key: getattr(context, key, absent) for key in ("user_id", "default_socket")}
        connect, send = socketutil.SocketUtil.connect, socketutil.send_msg
        category = "auth" if self._status_socket is None and cached is None else "product"
        used = set()
        observed = None

        def event(kind):
            value = dict(
                product_id=product_id, category=kind, network_attempts=1,
                auth_calls=int(kind == "auth"), sdk_calls=int(kind != "connection"),
                upstream_product_requests=int(kind == "product"), retries=0,
            )
            if kind == "product":
                value.update(api=api, params=params)
            return value

        def connect_metered(_self, api_key):
            if api_key != "0" or "connection" in used:
                raise RuntimeError("unexpected BaoStock connection")
            mark = event("connection")
            self.before_request(mark)
            used.add("connection")
            conn = None
            try:
                # Match the SDK's single IPv4 connect without create_connection's address fallback.
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.settimeout(30)
                conn.connect((contants.BAOSTOCK_SERVER_IP, contants.BAOSTOCK_SERVER_PORT))
            except Exception:
                if conn is not None:
                    conn.close()
                self.on_usage(dict(mark, failed=True))
                raise
            self._status_socket = context.default_socket = _StatusSocket(conn)
            self.on_usage(dict(mark, failed=False))

        def send_metered(message):
            nonlocal observed
            if category in used:
                raise RuntimeError("unexpected BaoStock SDK page or repeat")
            if cached is None:
                self.before_request(event(category))
            used.add(category)
            if cached is not None:
                packet = base64.b64decode(json.loads(cached["body"])["response_base64"], validate=True)
                boundary = contants.MESSAGE_HEADER_LENGTH
                footer = b"<![CDATA[]]>\n"
                header = packet[:boundary].decode()
                _, kind, size = header.split(contants.MESSAGE_SPLIT)
                payload_end = boundary + int(size)
                trailer = packet[payload_end:-len(footer)]
                if (
                    kind != response_kind
                    or len(packet) > wire_limit
                    or not boundary < payload_end <= len(packet) - len(footer)
                    or not packet.endswith(footer)
                    or (trailer and not re.fullmatch(rb"\x01[0-9]{1,10}\n", trailer))
                ):
                    raise ValueError("saved BaoStock response framing mismatch")
                value = header + zlib.decompress(packet[boundary:payload_end]).decode()
            else:
                if category == "product":
                    self._status_socket.wire_limit = wire_limit
                value = send(message)
                if category == "product":
                    observed = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            if not value:
                raise ConnectionError("BaoStock response incomplete or unreadable")
            # The SDK's history parser always removes the last character. Compressed
            # responses may omit the body newline, even when the TCP footer has one.
            if category == "product" and not whole_day and not value.endswith("\n"):
                value += "\n"
            return value

        def raw_evidence():
            packet = self._status_socket.received
            body = json.dumps({"response_base64": base64.b64encode(packet).decode("ascii")})
            return dict(body=body, api=api, params=params, obtained_at=observed, source="baostock",
                        sha256=hashlib.sha256(body.encode()).hexdigest())

        def require_success(result):
            if category not in used:
                raise ValueError(f"BaoStock SDK rejected {category} before sending")
            if result.error_code != "0":
                code = str(result.error_code)
                code = code if re.fullmatch(r"[0-9]{1,8}", code) else "unrecognized code"
                raise PermissionError(f"BaoStock rejected {category} ({code})")

        socketutil.SocketUtil.connect, socketutil.send_msg = connect_metered, send_metered
        try:
            if category == "auth":
                require_success(bs.login())
                self.on_usage(dict(event("auth"), failed=False))
            else:
                context.default_socket = self._status_socket
                context.user_id = "anonymous"
            category = "product"
            result = query(**params)
            require_success(result)
            if (
                result.method != api
                or str(result.cur_page_num) != "1"
                or any(len(row) != len(result.fields) for row in result.data)
            ):
                raise ValueError("BaoStock status schema or bounded response mismatch")
            if whole_day:
                required = {"date", "code", "tradestatus", "isST"}
                if not required <= set(result.fields) or len(set(result.fields)) != len(result.fields):
                    raise ValueError("BaoStock whole-day response lacks unambiguous status fields")
                if len(result.data) >= 20_000:
                    raise ValueError("BaoStock whole-day response reaches the SDK row limit")
            elif len(result.data) >= contants.BAOSTOCK_PER_PAGE_COUNT:
                raise ValueError("BaoStock single-code response reaches the SDK row limit")
            elif (
                result.fields != DAILY_FIELDS.split(",")
                or result.code != sdk_code
                or result.start_date != str(start)
                or result.end_date != str(end)
                or result.frequency != "d"
                or result.adjustflag != "3"
                or len(result.data) > (end - start).days + 1
            ):
                raise ValueError("BaoStock single-code status schema or bounded response mismatch")
            raw = pd.DataFrame(result.data, columns=result.fields)
            days = raw.date.map(date.fromisoformat)
            valid_codes = (
                raw.code.str.fullmatch(r"(?:sh|sz|bj)\.\d{6}", na=False) if whole_day else raw.code.eq(sdk_code)
            )
            if (
                not valid_codes.all()
                or not raw.date.eq(days.map(date.isoformat)).all()
                or not days.map(lambda day: start <= day <= end).all()
                or raw.duplicated(["date", "code"]).any()
                or not raw.tradestatus.isin(("0", "1")).all()
                or not raw.isST.isin(("0", "1")).all()
            ):
                raise ValueError("BaoStock status row outside requested code/dates or invalid flags")
            proof = cached if cached is not None else raw_evidence()
            if cached is None:
                self.on_usage(dict(event(category), failed=False, rows=len(raw), raw_response=proof))
            raw.attrs["provider_responses"] = [proof]
            if not whole_day and raw.empty:
                raw.attrs["query_observation"] = "success_empty"
            return raw
        except BaseException:
            try:
                if cached is None and category in used:
                    failure = dict(event(category), failed=True)
                    if category == "product" and self._status_socket.received:
                        failure["raw_response"] = raw_evidence()
                    self.on_usage(failure)
            finally:
                self._close_status_socket()
            raise
        finally:
            socketutil.SocketUtil.connect, socketutil.send_msg = connect, send
            for key, value in prior.items():
                if value is absent:
                    if hasattr(context, key):
                        delattr(context, key)
                else:
                    setattr(context, key, value)

    @staticmethod
    def _collect(rs) -> pd.DataFrame:
        if rs.error_code != "0":
            raise ConnectionError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=list(rs.fields))

    def fetch(self, product_id: str, codes: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
        with _SDK_LOCK:
            if self.before_request is not None or self.on_usage is not None:
                if (
                    self.before_request is None or self.on_usage is None
                    or product_id not in self.metered_products
                ):
                    raise ValueError("controlled BaoStock execution only supports metered status")
                return self._fetch_status_metered(product_id, codes, start, end)
            if product_id == "cn_a_status_day":
                raise ValueError("BaoStock whole-day status requires controlled execution")
            return self._fetch_legacy(product_id, codes, start, end)

    def _fetch_legacy(self, product_id, codes, start, end):
        bs = self._session()
        start_s, end_s = start.isoformat(), end.isoformat()
        if product_id == "cn_a_basic":
            return self._collect(bs.query_stock_basic())
        if product_id == "cn_trade_calendar":
            return self._collect(bs.query_trade_dates(start_date=start_s, end_date=end_s))
        if product_id in ("cn_a_daily", "cn_a_status"):
            frames = [
                self._collect(
                    bs.query_history_k_data_plus(
                        code.lower().replace(".SH", ".sh").replace(".SZ", ".sz").replace(".BJ", ".bj")
                        if "." in code
                        else code,
                        DAILY_FIELDS,
                        start_date=start_s,
                        end_date=end_s,
                        frequency="d",
                        adjustflag="3",
                    )
                )
                for code in codes
            ]
            frames = [frame for frame in frames if len(frame)]
            if not frames:
                return pd.DataFrame(columns=DAILY_FIELDS.split(","))
            return pd.concat(frames, ignore_index=True)
        if product_id == "csi300_price":
            targets = codes or ("sh.000300",)
            frames = [
                self._collect(
                    bs.query_history_k_data_plus(code, INDEX_FIELDS, start_date=start_s, end_date=end_s, frequency="d")
                )
                for code in targets
            ]
            frames = [frame for frame in frames if len(frame)]
            if not frames:
                return pd.DataFrame(columns=INDEX_FIELDS.split(","))
            return pd.concat(frames, ignore_index=True)
        if product_id == "cn_a_actions":
            frames = []
            for code in codes:
                bs_code = (
                    code.lower().replace(".SH", ".sh").replace(".SZ", ".sz").replace(".BJ", ".bj")
                    if "." in code
                    else code
                )
                for year in range(start.year, end.year + 1):
                    # yearType 实测（2026-10-16，.hetu/quant/qualification/
                    # capability-gap-20261016/actions-year-semantics-verdict.json）：
                    # 默认与 'report' 均匹配预案公告年（000400.SZ 公告 2024-11-20、
                    # 实施 2025-01-10 的事件只在 year=2024 返回），'operate' 匹配
                    # 除权除息实施年（只在 year=2025 返回）。收益构建按除息日窗口
                    # 消费事件，固定 operate 口径保证窗口内实施事件不因公告年
                    # 落在窗口前一年而漏采（此前默认口径已漏采此类事件的采集面，
                    # 已存快照对原公告年范围继续有效，见 05 计划 2026-10-16 记录）。
                    frames.append(
                        self._collect(bs.query_dividend_data(code=bs_code, year=str(year), yearType="operate"))
                    )
            frames = [frame for frame in frames if len(frame)]
            raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            # 只有上述实际 operate 查询全部成功后才附带证据；含成功空响应。
            # 不从当前配置给既有 raw/快照追补口径。
            raw.attrs["query_evidence"] = [
                {
                    "code": code,
                    "basis": "ex_date",
                    "start": start_s,
                    "end": end_s,
                    "method": "query_dividend_data",
                    "yearType": "operate",
                }
                for code in codes
            ]
            return raw
        raise KeyError(f"baostock provider does not serve product: {product_id}")

    def standardize(self, product_id: str, raw: pd.DataFrame, product: Mapping[str, object]) -> dict[str, pd.DataFrame]:
        if not len(raw):
            return {product["tables"][0]: raw}
        if product_id == "cn_a_basic":
            frame = pd.DataFrame(
                {
                    "code": raw["code"].map(normalize_code),
                    "name": raw["code_name"],
                    "market": raw["code"].str.split(".").str[0].str.upper(),
                    "ipo_date": raw["ipoDate"].replace("", None),
                    "out_date": raw["outDate"].replace("", None),
                    "security_type": raw["type"].map({"1": "stock", "2": "index", "3": "other"}),
                    "status": raw["status"].map({"1": "listed", "0": "delisted"}),
                }
            )
            return {"securities": frame}
        if product_id == "cn_trade_calendar":
            return {
                "calendar": pd.DataFrame(
                    {
                        "date": raw["calendar_date"],
                        "is_trading_day": raw["is_trading_day"] == "1",
                    }
                )
            }
        if product_id == "cn_a_daily":
            traded = raw[raw["tradestatus"] == "1"].copy()
            frame = pd.DataFrame(
                {
                    "date": traded["date"],
                    "code": traded["code"].map(normalize_code),
                    "open_raw": pd.to_numeric(traded["open"], errors="coerce"),
                    "high_raw": pd.to_numeric(traded["high"], errors="coerce"),
                    "low_raw": pd.to_numeric(traded["low"], errors="coerce"),
                    "close_raw": pd.to_numeric(traded["close"], errors="coerce"),
                    "volume_shares": pd.to_numeric(traded["volume"], errors="coerce"),
                    "amount_cny": pd.to_numeric(traded["amount"], errors="coerce"),
                }
            )
            return {"daily_bars": frame}
        if product_id in ("cn_a_status", "cn_a_status_day"):
            frame = pd.DataFrame(
                {
                    "date": raw["date"],
                    "code": raw["code"].map(normalize_code),
                    "is_st": raw["isST"] == "1",
                    "is_suspended": raw["tradestatus"] == "0",
                    "is_listed": True,
                }
            )
            return {"security_status": frame}
        if product_id == "csi300_price":
            frame = pd.DataFrame(
                {
                    "code": raw["code"].map(normalize_code),
                    "date": raw["date"],
                    "close": pd.to_numeric(raw["close"], errors="coerce"),
                    "amount_cny": pd.to_numeric(raw["amount"], errors="coerce"),
                    "return_basis": "price",
                }
            )
            return {"index_bars": frame}
        if product_id == "cn_a_actions":
            raw = dedupe_dividend_frame(raw)
            rows = []
            for _, row in raw.iterrows():
                cash = pd.to_numeric(pd.Series([row.get("dividCashPsBeforeTax")]), errors="coerce").iloc[0]
                stocks = pd.to_numeric(pd.Series([row.get("dividStocksPs")]), errors="coerce").iloc[0]
                reserve = pd.to_numeric(pd.Series([row.get("dividReserveToStockPs")]), errors="coerce").iloc[0]
                cash = 0.0 if pd.isna(cash) else float(cash)
                m = 1.0 + (0.0 if pd.isna(stocks) else float(stocks)) + (0.0 if pd.isna(reserve) else float(reserve))
                notice_dates = [
                    str(row.get(key) or "")
                    for key in ("dividPreNoticeDate", "dividPlanAnnounceDate", "dividAgmPumDate")
                ]
                available = min((d for d in notice_dates if d), default=None)
                ex_date = str(row.get("dividOperateDate") or "") or None
                code = normalize_code(str(row["code"]))
                terms = str(row.get("dividCashStock") or "")
                action_dates = {
                    "record_date": str(row.get("dividRegistDate") or "") or None,
                    "pay_date": str(row.get("dividPayDate") or "") or None,
                    "stock_listing_date": str(row.get("dividStockMarketDate") or "") or None,
                }
                if cash > 0:
                    rows.append(
                        {
                            "code": code,
                            "ex_date": ex_date,
                            "action_type": "cash_dividend",
                            "m": 1.0,
                            "d": cash,
                            "terms_raw": terms,
                            "available_at": available,
                            **action_dates,
                        }
                    )
                if m != 1.0:
                    rows.append(
                        {
                            "code": code,
                            "ex_date": ex_date,
                            "action_type": "bonus_split",
                            "m": m,
                            "d": 0.0,
                            "terms_raw": terms,
                            "available_at": available,
                            **action_dates,
                        }
                    )
            frame = pd.DataFrame(
                rows,
                columns=[
                    "code",
                    "ex_date",
                    "action_type",
                    "m",
                    "d",
                    "terms_raw",
                    "available_at",
                    "record_date",
                    "pay_date",
                    "stock_listing_date",
                ],
            )
            if len(frame):
                # 同一实施日可有多条记录（如中期+年度分红、多轮公告），
                # 按主键合并：现金合并计、m 连乘、可得时点取最早、条款并集保留。
                def _earliest(values: pd.Series):
                    known = [str(v) for v in values if str(v or "")]
                    return min(known) if known else None

                def _one_date(values: pd.Series):
                    known = {str(v) for v in values if pd.notna(v) and str(v)}
                    if len(known) > 1:
                        raise ValueError(f"conflicting corporate action dates: {sorted(known)}")
                    return next(iter(known), None)

                frame = frame.groupby(["code", "ex_date", "action_type"], as_index=False).agg(
                    m=("m", "prod"),
                    d=("d", "sum"),
                    terms_raw=(
                        "terms_raw",
                        lambda s: ";".join(sorted({str(v) for v in s if str(v or "")})),
                    ),
                    available_at=("available_at", _earliest),
                    record_date=("record_date", _one_date),
                    pay_date=("pay_date", _one_date),
                    stock_listing_date=("stock_listing_date", _one_date),
                )
            return {"corporate_actions": frame}
        raise KeyError(f"no standardize mapping for product: {product_id}")
