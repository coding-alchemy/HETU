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
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from rqalpha.const import MARKET, TRADING_CALENDAR_TYPE
from rqalpha.data.base_data_source.data_source import BaseDataSource
from rqalpha.model.instrument import Instrument
from rqalpha.utils.datetime_func import convert_date_to_int

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
        self._dividends_by_code: dict[str, np.ndarray] = {}
        self._splits_by_code: dict[str, np.ndarray] = {}
        self.action_events: dict[str, list[dict]] = {}
        self._build_actions(actions)
        self.terminal_disposals = {}
        disposal_path = tables_dir / "terminal_disposals.parquet"
        if disposal_path.exists():
            for row in pd.read_parquet(disposal_path).to_dict("records"):
                evidence = (bundle_dir / "evidence" / str(row["evidence_ref"])).resolve()
                if (not evidence.is_relative_to((bundle_dir / "evidence").resolve())
                        or not evidence.is_file()
                        or hashlib.sha256(evidence.read_bytes()).hexdigest() != row["sha256"]):
                    raise ValueError("terminal_disposal_evidence_unproven")
                if pd.Timestamp(row["available_at"]) > pd.Timestamp(str(row["date"]) + "T00:00:00+08:00"):
                    raise ValueError("terminal_disposal_not_known_at_event")
                if float(row["cash_per_share"]) < 0:
                    raise ValueError("terminal_disposal_negative_compensation")
                self.terminal_disposals[(str(row["code"]), str(row["date"]))] = row

    # ---- 构建 ----

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
