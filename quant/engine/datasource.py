"""二期 02.1 本地 bundle 数据源：只读 prepare_backtest 产出的已核验表。

消费生产布局（prepare_backtest 合同，不改）：
- 根级：definition.json、signals.parquet、admission.json（探查／测试直跑用；
  生产经 run_backtest 在暂存目录写入 admission.json）；
- tables/：calendar、daily_bars、security_status、historical_securities、
  corporate_actions、trading_rules（由 rules.py 解析，见 mod.py／runner.py）。

覆盖引擎实际调用的接口：证券、交易日历、日线 bar 与历史 bar、ST／停牌、
现金分红与送转的登记、除息、实际派息与可卖日期；必需日期缺失明确拒绝。
不向 rqdatac 或网络
请求任何补数；未支持域（期货、tick、集合竞价、收益率曲线）显式拒绝。
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from rqalpha.const import MARKET, TRADING_CALENDAR_TYPE
from rqalpha.data.base_data_source.data_source import BaseDataSource
from rqalpha.model.instrument import Instrument
from rqalpha.utils.datetime_func import convert_date_to_int

# The isolated engine does not install the workbench package.
_QUANT_SRC = str(Path(__file__).resolve().parents[1] / "src" / "hetu_quant")
if _QUANT_SRC not in sys.path:
    sys.path.insert(0, _QUANT_SRC)
from account_evidence import account_dispositions, execution_cutoff  # noqa: E402

_BAR_DTYPE = np.dtype(
    [
        ("datetime", "<u8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("volume", "<f8"),
        ("total_turnover", "<f8"),
    ]
)

_DIVIDEND_DTYPE = np.dtype(
    [
        ("book_closure_date", "<i8"),
        ("announcement_date", "<i8"),
        ("dividend_cash_before_tax", "<f8"),
        ("ex_dividend_date", "<i8"),
        ("payable_date", "<i8"),
        ("round_lot", "<f8"),
    ]
)

_SPLIT_DTYPE = np.dtype([("ex_date", "<i8"), ("split_factor", "<f8")])


class LimitReferenceError(ValueError):
    """无法从前收和已核验行动形成当日除权参考价。"""


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("true", "1")


def _day_int(day: str) -> int:
    return int(str(day).replace("-", "")[:8])


class LocalBundleDataSource(BaseDataSource):
    """以 HETU 生产 bundle 为唯一数据源的 RQAlpha DataSource。

    故意不调用 ``BaseDataSource.__init__``：后者要求官方 bundle 目录布局
    （stocks.h5 等），与本项目已核验的 parquet 输入包无关；本类只复用基类中
    不依赖官方 bundle 的查询逻辑（如 ``get_bar`` 的按日定位）。
    """

    def __init__(self, bundle_dir: Path) -> None:
        self._market = MARKET.CN
        bundle_dir = Path(bundle_dir)
        tables_dir = bundle_dir / "tables"

        calendar = pd.read_parquet(tables_dir / "calendar.parquet")
        trading_days = [
            str(day)
            for day, flag in zip(calendar["date"].astype(str), calendar["is_trading_day"], strict=False)
            if _truthy(flag)
        ]
        if not trading_days:
            raise ValueError(f"bundle 日历无交易日: {tables_dir / 'calendar.parquet'}")
        self._calendar = pd.DatetimeIndex(pd.to_datetime(trading_days))
        self._day_rank = {day: index for index, day in enumerate(trading_days)}

        securities = pd.read_parquet(tables_dir / "historical_securities.parquet")
        self.identity_transitions = self._read_identity_transitions(bundle_dir, securities)
        self.renames_from = {event["from_code"]: event for event in self.identity_transitions.values()}
        self._id_instrument_map: dict[str, dict[datetime, Instrument]] = {}
        self._sym_instrument_map: dict[str, dict[datetime, Instrument]] = {}
        self._grouped_instruments: dict[object, list[Instrument]] = {}
        from collections import ChainMap

        self._id_or_sym_instrument_map = ChainMap(
            self._id_instrument_map, self._sym_instrument_map
        )
        instruments = []
        for item in securities.itertuples():
            code = str(item.code)
            valid_from = str(item.valid_from)
            if valid_from in ("", "None", "nan", "NaN", "NaT", "<NA>"):
                valid_from = "1990-01-01"
            valid_to = str(item.valid_to)
            if valid_to in ("", "None", "nan", "NaN", "NaT", "<NA>"):
                valid_to = "2999-12-31"
            else:
                # The source gives the last included date, while RQAlpha uses an exclusive boundary.
                valid_to = (date.fromisoformat(valid_to) + timedelta(days=1)).isoformat()
            instruments.append(
                Instrument(
                    {
                        "order_book_id": code,
                        "symbol": code,
                        "type": "CS",
                        # 申报单位由 trading_rules 在策略层按板/日期执行
                        #（科创板 200 起 1 股递增）；引擎 lot 取 1 不二次取整
                        "round_lot": 1,
                        "listed_date": valid_from,
                        "de_listed_date": valid_to,
                        "exchange": "XSHE" if str(item.market).upper() == "SZ" else "XSHG",
                        "board_type": "MainBoard",
                        "status": "Active",
                        "special_type": "Normal",
                        "market_tplus": 1,
                    }
                )
            )
        self.register_instruments(instruments)

        bars = pd.read_parquet(tables_dir / "daily_bars.parquet")
        self._bars_by_code = self._build_bars(bars)
        self._prev_close: dict[str, dict[str, float]] = {}
        for code, array in self._bars_by_code.items():
            closes: dict[str, float] = {}
            for value in array:
                closes[str(value["datetime"])[:8]] = float(value["close"])
            self._prev_close[code] = closes

        status_path = tables_dir / "security_status.parquet"
        self._st_days: dict[str, set[str]] = {}
        self._suspended_days: dict[str, set[str]] = {}
        if status_path.exists():
            status = pd.read_parquet(status_path)
            for row in status.itertuples():
                code = str(row.code)
                day = str(row.date)
                if _truthy(getattr(row, "is_st", False)):
                    self._st_days.setdefault(code, set()).add(day)
                if _truthy(getattr(row, "is_suspended", False)):
                    self._suspended_days.setdefault(code, set()).add(day)

        actions_path = tables_dir / "corporate_actions.parquet"
        actions = pd.read_parquet(actions_path) if actions_path.exists() else pd.DataFrame(
            columns=["code", "ex_date", "action_type", "m", "d"]
        )
        definition = json.loads((bundle_dir / "definition.json").read_text(encoding="utf-8"))
        account_start = pd.Timestamp(definition.get("start") or trading_days[0])
        account_end = pd.Timestamp(definition.get("end") or trading_days[-1])
        # 预热行动保留在原表供研究收益使用。账户从定义的起点持有现金，
        # 只对执行区间内的事件建账；不能确认实施日的行继续交给日期校验。
        ex_dates = pd.to_datetime(actions["ex_date"], errors="coerce")
        account_actions = actions[~(ex_dates.lt(account_start) | ex_dates.gt(account_end))]
        self._dividends_by_code: dict[str, np.ndarray] = {}
        self._splits_by_code: dict[str, np.ndarray] = {}
        self.action_events: dict[str, list[dict]] = {}
        self._build_actions(account_actions)
        as_of = pd.Timestamp(definition["as_of"]) if definition.get("as_of") else None
        disposal_path = tables_dir / "terminal_disposals.parquet"
        exchange_path = tables_dir / "share_exchanges.parquet"
        self.terminal_disposals, self.share_exchanges, cash_gaps, exchange_gaps, conflicts = account_dispositions(
            pd.read_parquet(disposal_path).to_dict("records") if disposal_path.exists() else [],
            pd.read_parquet(exchange_path).to_dict("records") if exchange_path.exists() else [],
            bundle_dir / "evidence", account_start.date().isoformat(), account_end.date().isoformat(), as_of=as_of,
        )
        self.disposition_gaps = [*cash_gaps, *exchange_gaps, *conflicts]
        if self.terminal_disposals:
            for (code, day), row in list(self.terminal_disposals.items()):
                outgoing = self.renames_from.get(code)
                if outgoing and outgoing["from_valid_to"] <= day <= outgoing["effective_date"]:
                    del self.terminal_disposals[code, day]
                    del self.identity_transitions[outgoing["code"]]
                    del self.renames_from[code]
                    self.disposition_gaps.append({
                        "code": code, "date": day, "reason": "identity_transition_conflicts_with_cash_disposal",
                        "source_refs": row["source_refs"] + [outgoing["evidence_ref"]],
                    })
        if self.share_exchanges:
            for (code, day), event in list(self.share_exchanges.items()):
                known_code = event.get("code")
                invalid_lifecycle = known_code and (
                    not (securities.code.eq(code) & securities.valid_to.astype(str).le(day)).any()
                    or not (securities.code.eq(known_code) & securities.valid_from.astype(str).le(day)
                            & (securities.valid_to.astype("string").fillna("").eq("") |
                               securities.valid_to.astype(str).ge(day))).any()
                )
                rename = self.renames_from.get(code)
                cash = [key for key in self.terminal_disposals if key[0] == code and key[1] >= day]
                if invalid_lifecycle or rename or cash:
                    if rename:
                        del self.identity_transitions[rename["code"]]
                        del self.renames_from[code]
                    for key in cash:
                        del self.terminal_disposals[key]
                    event["ratio"] = None
                    self.disposition_gaps.append({
                        "code": code, "date": day, "reason": "share_exchange_lifecycle_or_disposition_conflict",
                        "source_refs": event["source_refs"],
                    })

    # ---- 构建 ----

    def _read_identity_transitions(self, bundle_dir, securities):
        path = bundle_dir / "tables" / "identity_transitions.parquet"
        if not path.is_file():
            return {}
        events = {}
        outgoing = set()
        for row in pd.read_parquet(path).to_dict("records"):
            code, old, day = (str(row[key]) for key in ("code", "from_code", "effective_date"))
            cutoff = execution_cutoff(day)
            original = (bundle_dir / "evidence" / row["evidence_ref"]).resolve()
            if not original.is_relative_to((bundle_dir / "evidence").resolve()) or not original.is_file():
                raise ValueError("identity_transition_evidence_unproven")
            raw = original.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("identity_transition_evidence_unproven")
            records = json.loads(raw)
            if isinstance(records, dict):
                records = records["objects"]
            old_rows = [item for item in records if str(item.get("code")) == old]
            new_rows = [item for item in records if str(item.get("code")) == code]
            previous = str(row["from_valid_to"])
            attrs = ("kind", "from_code", "effective_date", "published_at", "ratio")
            publication = pd.Timestamp(row["published_at"])
            if (
                len(old_rows) != 1 or len(new_rows) != 1 or old == code or not row["identity_id"]
                or old_rows[0].get("identity_id") != row["identity_id"]
                or new_rows[0].get("identity_id") != row["identity_id"]
                or old_rows[0].get("valid_to", old_rows[0].get("out_date")) != previous
                or new_rows[0].get("valid_from", new_rows[0].get("ipo_date")) != day
                or any((new_rows[0].get("continuation") or {}).get(name) != row[name] for name in attrs)
                or row["kind"] != "administrative_rename" or row["ratio"] != 1 or isinstance(row["ratio"], bool)
                or pd.isna(publication) or publication.tzinfo is None
                or not self._day_rank.get(day) or previous >= day
                or self._calendar[self._day_rank[day] - 1].date().isoformat() > previous
                or not (securities.code.eq(old) & securities.valid_to.astype(str).eq(previous)).any()
                or not (securities.code.eq(code) & securities.valid_from.astype(str).eq(day)).any()
                or old in outgoing or code in events
            ):
                raise ValueError("identity_transition_terms_unproven")
            if publication > cutoff:
                continue
            events[code] = row
            outgoing.add(old)
        return events

    def _build_bars(self, bars: pd.DataFrame) -> dict[str, np.ndarray]:
        result: dict[str, np.ndarray] = {}
        for code, group in bars.groupby(bars["code"].astype(str)):
            group = group.sort_values("date")
            array = np.zeros(len(group), dtype=_BAR_DTYPE)
            array["datetime"] = [convert_date_to_int(pd.Timestamp(str(d))) for d in group["date"].astype(str)]
            open_raw = group["open_raw"].astype(float).to_numpy()
            close_raw = group["close_raw"].astype(float).to_numpy()
            array["open"] = open_raw
            array["close"] = close_raw
            if "high_raw" in group.columns and "low_raw" in group.columns:
                array["high"] = group["high_raw"].astype(float).to_numpy()
                array["low"] = group["low_raw"].astype(float).to_numpy()
            else:
                # 只有开／收的输入：高低价以开收夹逼（机制输入的日线近似）
                array["high"] = np.maximum(open_raw, close_raw)
                array["low"] = np.minimum(open_raw, close_raw)
            array["volume"] = group["volume_shares"].astype(float).to_numpy()
            array["total_turnover"] = array["volume"] * close_raw
            result[code] = array
        return result

    def _build_actions(self, actions: pd.DataFrame) -> None:
        """保留生产 m=新股数/原股数；日期与权益交由持仓扩展消费，不造日期。"""
        ordered = actions.assign(_priority=actions["action_type"].map(
            {"cash_dividend": 0, "bonus_split": 1})).sort_values(["ex_date", "_priority"])
        for row in ordered.to_dict("records"):
            action = str(row["action_type"])
            required = ["record_date", "ex_date"]
            if action == "cash_dividend":
                required.append("pay_date")
            elif action == "bonus_split":
                required.append("stock_listing_date")
            else:
                raise ValueError(f"公司行动类型未核验: {action}")
            event = dict(row)
            for field in required:
                value = row.get(field)
                if pd.isna(value) or str(value) in ("", "None", "nan", "<NA>"):
                    raise ValueError(f"corporate_action_dates_unproven: {row['code']} 缺 {field}")
                event[field] = pd.Timestamp(str(value)).date()
            if event["record_date"] >= event["ex_date"]:
                raise ValueError("登记日不早于除息日，权益时点未核验")
            self.action_events.setdefault(str(row["code"]), []).append(event)
        # 权益由StockPosition扩展按登记快照处理；禁止同时触发引擎默认的同日全持仓处理。

    def _next_trading_day(self, day: str) -> str | None:
        rank = self._day_rank.get(str(day))
        if rank is None or rank + 1 >= len(self._calendar):
            return None
        return str(self._calendar[rank + 1].date())

    # ---- 日历与证券 ----

    def get_trading_calendars(self) -> dict[TRADING_CALENDAR_TYPE, pd.DatetimeIndex]:
        return {TRADING_CALENDAR_TYPE.CN_STOCK: self._calendar}

    def available_data_range(self, frequency: str) -> tuple[date, date]:
        return self._calendar[0].date(), self._calendar[-1].date()

    # ---- 行情 ----

    def limit_reference_price(self, code: str, dt: datetime) -> float:
        """限制价专用参考：普通日前收，除权日 (原前收-每原股现金)/新原股数比。

        不改账户bar原价，不使用忽略复权参数的DataProxy.get_prev_close。
        输入仍须满足生产包的行动覆盖／日期合同；缺值或不能解释的组合不回落原前收。
        """
        day = dt.date()
        reason = f"limit_reference_unproven: {code} @ {day}"
        index = self._calendar.searchsorted(pd.Timestamp(day))
        if index == 0:
            raise LimitReferenceError(f"{reason}: 缺前一交易日")
        previous_day = self._calendar[index - 1].strftime("%Y%m%d")
        event = self.identity_transitions.get(code)
        previous_code = event["from_code"] if event and event["effective_date"] == day.isoformat() else code
        previous = self._prev_close.get(previous_code, {}).get(previous_day, float("nan"))
        if not math.isfinite(previous) or previous <= 0:
            raise LimitReferenceError(f"{reason}: 缺前一交易日原收盘")
        cash, ratio = Decimal("0"), Decimal("1")
        splits = 0
        for event in self.action_events.get(code, []):
            if event["ex_date"] != day:
                continue
            field = "d" if event["action_type"] == "cash_dividend" else "m"
            try:
                value = float(event[field])
            except (TypeError, ValueError, KeyError) as exc:
                raise LimitReferenceError(f"{reason}: 行动 {field} 未知") from exc
            if not math.isfinite(value) or value < 0 or (field == "m" and value == 0):
                raise LimitReferenceError(f"{reason}: 行动 {field} 无有效数值")
            if field == "d":
                cash += Decimal(str(value))
            else:
                splits += 1
                if splits > 1:
                    raise LimitReferenceError(f"{reason}: 同日多送转比例关系未证明")
                ratio = Decimal(str(value))
        reference = float((Decimal(str(previous)) - cash) / ratio)
        if not math.isfinite(reference) or reference <= 0:
            raise LimitReferenceError(f"{reason}: 除权参考价非正或无效")
        return reference

    def _all_day_bars_of(self, instrument: Instrument) -> np.ndarray:
        return self._bars_by_code.get(instrument.order_book_id, np.zeros(0, dtype=_BAR_DTYPE))

    def history_bars(
        self,
        instrument: Instrument,
        bar_count: int | None,
        frequency: str,
        fields: str | list[str] | None,
        dt: datetime,
        skip_suspended: bool = True,
        include_now: bool = False,
        adjust_type: str = "pre",
        adjust_orig: datetime | None = None,
    ) -> np.ndarray | None:
        """历史 bar 查询（原价，不做复权调整：账户轨迹用原始价格）。

        窗口语义与官方 BaseDataSource 1d 分支一致：side='right' 包含 dt 当日
        bar；include_now 仅周频生效（1d 忽略）。
        """
        if frequency != "1d":
            return None
        bars = self._all_day_bars_of(instrument)
        if not len(bars):
            return None
        dt_int = np.uint64(convert_date_to_int(dt))
        end = int(bars["datetime"].searchsorted(dt_int, side="right"))
        start = max(0, end - bar_count) if bar_count is not None else 0
        window = bars[start:end]
        if isinstance(fields, str):
            return window[fields]
        if fields is None:
            return window
        return window[list(fields)]

    def is_suspended(self, order_book_id: str, dates) -> list[bool]:
        suspended = self._suspended_days.get(str(order_book_id), set())
        return [str(d) in suspended for d in dates]

    def is_st_stock(self, order_book_id: str, dates) -> list[bool]:
        st_days = self._st_days.get(str(order_book_id), set())
        return [str(d) in st_days for d in dates]

    # ---- 公司行动 ----

    def get_dividend(self, instrument: Instrument):
        return self._dividends_by_code.get(instrument.order_book_id)

    def get_split(self, instrument: Instrument):
        return self._splits_by_code.get(instrument.order_book_id)

    def get_share_transformation(self, order_book_id: str):
        return None

    def get_ex_cum_factor(self, instrument: Instrument):
        return None

    # ---- 未支持域：显式拒绝，不借官方 bundle 补缺 ----

    def get_yield_curve(self, start_date, end_date, tenor=None):
        raise NotImplementedError("本地输入包不含国债收益率曲线")

    def get_trading_minutes_for(self, instrument, trading_dt):
        raise NotImplementedError("本地输入包不含日内交易时段（仅日线）")
