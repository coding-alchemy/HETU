"""BaoStock 适配器：原始返回保留原形，标准化映射在 provider 内完成。

实测要点（2026-10-14，evidence 见 .hetu/quant/qualification/）：
- query_stock_basic().get_data() 内部使用 pandas 已移除的 DataFrame.append，
  本适配器统一用 rs.next()/get_row_data() 逐行收集。
- volume 单位为股，amount 单位为元人民币；adjustflag='3' 为不复权原价。
- 分红 query_dividend_data 提供税前每股现金、每股送转股数及各公告/实施日期。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pandas as pd

DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,isST"
)
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"


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

    def __init__(self) -> None:
        self._bs = None

    def _session(self):
        if self._bs is None:
            import baostock as bs

            login = bs.login()
            if login.error_code != "0":
                raise ConnectionError(f"baostock login failed: {login.error_msg}")
            self._bs = bs
        return self._bs

    def close(self) -> None:
        if self._bs is not None:
            self._bs.logout()
            self._bs = None

    @staticmethod
    def _collect(rs) -> pd.DataFrame:
        if rs.error_code != "0":
            raise ConnectionError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=list(rs.fields))

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
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
                    bs.query_history_k_data_plus(
                        code, INDEX_FIELDS, start_date=start_s, end_date=end_s, frequency="d"
                    )
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
                bs_code = code.lower().replace(".SH", ".sh").replace(".SZ", ".sz").replace(
                    ".BJ", ".bj"
                ) if "." in code else code
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
                        self._collect(
                            bs.query_dividend_data(
                                code=bs_code, year=str(year), yearType="operate"
                            )
                        )
                    )
            frames = [frame for frame in frames if len(frame)]
            raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            # 只有上述实际 operate 查询全部成功后才附带证据；含成功空响应。
            # 不从当前配置给既有 raw/快照追补口径。
            raw.attrs["query_evidence"] = [
                {"code": code, "basis": "ex_date", "start": start_s, "end": end_s,
                 "method": "query_dividend_data", "yearType": "operate"}
                for code in codes
            ]
            return raw
        raise KeyError(f"baostock provider does not serve product: {product_id}")

    def standardize(
        self, product_id: str, raw: pd.DataFrame, product: Mapping[str, object]
    ) -> dict[str, pd.DataFrame]:
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
                    "security_type": raw["type"].map(
                        {"1": "stock", "2": "index", "3": "other"}
                    ),
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
        if product_id == "cn_a_status":
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
                cash = pd.to_numeric(
                    pd.Series([row.get("dividCashPsBeforeTax")]), errors="coerce"
                ).iloc[0]
                stocks = pd.to_numeric(
                    pd.Series([row.get("dividStocksPs")]), errors="coerce"
                ).iloc[0]
                reserve = pd.to_numeric(
                    pd.Series([row.get("dividReserveToStockPs")]), errors="coerce"
                ).iloc[0]
                cash = 0.0 if pd.isna(cash) else float(cash)
                m = 1.0 + (0.0 if pd.isna(stocks) else float(stocks)) + (
                    0.0 if pd.isna(reserve) else float(reserve)
                )
                notice_dates = [
                    str(row.get(key) or "")
                    for key in ("dividPreNoticeDate", "dividPlanAnnounceDate", "dividAgmPumDate")
                ]
                available = min((d for d in notice_dates if d), default=None)
                ex_date = str(row.get("dividOperateDate") or "") or None
                code = normalize_code(str(row["code"]))
                terms = str(row.get("dividCashStock") or "")
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
                        }
                    )
            frame = pd.DataFrame(
                rows,
                columns=[
                    "code", "ex_date", "action_type", "m", "d",
                    "terms_raw", "available_at",
                ],
            )
            if len(frame):
                # 同一实施日可有多条记录（如中期+年度分红、多轮公告），
                # 按主键合并：现金合并计、m 连乘、可得时点取最早、条款并集保留。
                def _earliest(values: pd.Series):
                    known = [str(v) for v in values if str(v or "")]
                    return min(known) if known else None

                frame = (
                    frame.groupby(["code", "ex_date", "action_type"], as_index=False)
                    .agg(
                        m=("m", "prod"),
                        d=("d", "sum"),
                        terms_raw=(
                            "terms_raw",
                            lambda s: ";".join(sorted({str(v) for v in s if str(v or "")})),
                        ),
                        available_at=("available_at", _earliest),
                    )
                )
            return {"corporate_actions": frame}
        raise KeyError(f"no standardize mapping for product: {product_id}")
