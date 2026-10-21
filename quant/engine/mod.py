"""二期 02.1 引擎 Mod：本地数据源、次日开盘撮合、协议费用与涨跌停。

撮合说明：RQAlpha 6.4.1 日线 current_bar 模式在订单报送时立即撮合，1d 的
next_bar 被显式废弃。策略在 executable_from 日盘前以当日开盘限价报送，
本 Mod 的 Matcher 以成交日开盘价加模拟滑点并按不利方向取整到适用步长（execution.fill_time=next_open），
并按 trading_rules 的 limit_rule 在开盘处于不利限制价时保守拒绝：
涨停价开盘拒绝买入、跌停价开盘拒绝卖出（一字排队不可证明，不假设成交）。

费用说明：佣金按协议（definition.costs，含最低佣金，按订单一次计收）、
印花税卖出单边、过户费双边，费率按 trading_rules fee_rule 逐日期取值；
滑点进入模拟成交价，费用仅作独立归因而不再扣现金（日线近似，见
engine-semantics.json）。股息红利差别化个税由股票持仓扩展
按登记权益批次计提、卖出时按持有期20%/10%/0%扣缴（fee_rule dividend=holding_diff 时
启用；dividend=zero 为合成零税制）。

本 Mod 必须在 sys_simulation／sys_transaction_cost 之后启动
（priority 更大）以注册 broker Matcher 与费用决定器，并在 main 装配
DataProxy 之前替换数据源。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rqalpha.const import INSTRUMENT_TYPE, SIDE
from rqalpha.interface import (
    AbstractMod,
    TransactionCost,
    TransactionCostArgs,
)
from rqalpha.mod.rqalpha_mod_sys_simulation.matcher.bar_matcher import DefaultBarMatcher
from rqalpha.mod.rqalpha_mod_sys_simulation.matcher.base import OrderRejected
from rqalpha.mod.rqalpha_mod_sys_transaction_cost.deciders import (
    AbstractStockTransactionCostDecider,
)

try:  # 包内导入（测试）与脚本旁路导入（runner 以文件路径加载本模块）均可
    from .datasource import LocalBundleDataSource
    from .position import HetuStockPosition
    from .rules import BundleRules, load_bundle_rules
except ImportError:  # pragma: no cover - 取决于加载方式
    from datasource import LocalBundleDataSource  # type: ignore[no-redef]
    from position import HetuStockPosition  # type: ignore[no-redef]
    from rules import BundleRules, load_bundle_rules  # type: ignore[no-redef]


class NextOpenBarMatcher(DefaultBarMatcher):
    """普通股日线撮合：开盘加滑点，按不利方向取整；开盘不利限制价或滑点越界拒绝。

    涨停价开盘（开盘价 ≥ 涨停价）拒绝买入；跌停价开盘（开盘价 ≤ 跌停价）
    拒绝卖出。涨停价／跌停价按 trading_rules limit_rule 与前一交易日原价
    收盘计算（0.01 元最小变动单位四舍五入）。成交量限制沿用 sys_simulation
    配置（volume_limit/volume_percent）。
    """

    def __init__(self, env, mod_config, rules: BundleRules, partial_fill_on_insufficient_cash=False):
        super().__init__(env, mod_config, partial_fill_on_insufficient_cash)
        self._rules = rules

    def _create_deal_price_decider(self, matching_type):
        # 成交价固定为成交日开盘价（次交易日开盘成交的日线实现）
        return self._next_bar_open_decider

    def _get_deal_price(self, order, instrument, open_auction: bool = False) -> float:
        deal_price = super()._get_deal_price(order, instrument, open_auction)
        day = self._env.trading_dt.date().isoformat()
        pct = self._rules.limit_pct(instrument.order_book_id, day)
        try:
            prev_close = float(self._env.data_proxy.get_prev_close(instrument.order_book_id, self._env.trading_dt))
        except (TypeError, ValueError, KeyError):
            prev_close = float("nan")
        if not np.isfinite(prev_close) or prev_close <= 0:
            raise OrderRejected(f"{instrument.order_book_id}: 缺前一交易日收盘，无法核验涨跌幅限制")
        limit_up, limit_down = self._rules.limit_price(
            prev_close, pct, self._rules.tick_size(instrument.order_book_id, day))
        if order.side == SIDE.BUY and deal_price >= limit_up - 1e-9:
            raise OrderRejected(
                f"{instrument.order_book_id}: 开盘涨停（{deal_price} ≥ 涨停价 {limit_up}），买入保守拒绝"
            )
        if order.side == SIDE.SELL and deal_price <= limit_down + 1e-9:
            raise OrderRejected(
                f"{instrument.order_book_id}: 开盘跌停（{deal_price} ≤ 跌停价 {limit_down}），卖出保守拒绝"
            )
        rate = float(self._env.config.mod.hetu_local_bundle.slippage_rate)
        slipped = self._rules.slippage_price(
            instrument.order_book_id, day, deal_price, rate, buy=order.side == SIDE.BUY)
        if slipped > limit_up + 1e-9 or slipped < limit_down - 1e-9 or slipped <= 0:
            raise OrderRejected(
                f"{instrument.order_book_id}: 含滑点价格 {slipped} 超出合法价格区间 [{limit_down}, {limit_up}]"
            )
        return slipped


class HetuStockCostDecider(AbstractStockTransactionCostDecider):
    """协议费用决定器：佣金（含最低，按订单一次计收）＋印花（卖出）＋过户（双边）＋滑点成本。

    印花／过户费率从 trading_rules fee_rule 按成交日取值；滑点已在成交价中，
    不再经费用通道扣除；三类显式费用与滑点归因留存在 breakdown 中，runner 逐笔写入轨迹表，
    保证 trades 费用列与 costs 表逐笔一致（单一数据源）。
    """

    def __init__(
        self,
        env,
        rules: BundleRules,
        *,
        commission_rate: float,
        min_commission: float,
        slippage_rate: float,
    ) -> None:
        self._env = env
        self._rules = rules
        self._commission_rate = float(commission_rate)
        self._min_commission = float(min_commission)
        self._slippage_rate = float(slippage_rate)
        self._commission_seen: set[int] = set()
        self._breakdown: dict[int, list[float]] = {}

    def breakdown_for(self, order_id) -> tuple[float, float, float, float]:
        """(commission, stamp_tax, transfer_fee, slippage_cost) 分项，供轨迹表逐笔登记。"""
        values = self._breakdown.get(int(order_id), [0.0, 0.0, 0.0, 0.0])
        return (values[0], values[1], values[2], values[3])

    def calc(self, args: TransactionCostArgs) -> TransactionCost:
        day = self._env.trading_dt.date().isoformat()
        fees = self._rules.fee_components(args.instrument.order_book_id, day)
        amount = float(args.price) * float(args.quantity)
        raw_commission = amount * self._commission_rate
        oid = int(args.order_id) if args.order_id is not None else id(args)
        if oid not in self._commission_seen:
            # 最低佣金按订单一次计收（同订单后续成交不再重复收取）
            commission = max(raw_commission, self._min_commission) if amount > 0 else 0.0
            self._commission_seen.add(oid)
        else:
            commission = raw_commission
        stamp = amount * float(fees["stamp_rate"]) if args.side == SIDE.SELL else 0.0
        transfer = amount * float(fees["transfer_rate"])
        raw_price = float(self._env.get_bar(args.instrument.order_book_id).open)
        slippage = abs(float(args.price) - raw_price) * float(args.quantity)
        entry = self._breakdown.setdefault(oid, [0.0, 0.0, 0.0, 0.0])
        entry[0] += commission
        entry[1] += stamp
        entry[2] += transfer
        entry[3] += slippage
        return TransactionCost(commission=commission, tax=stamp + transfer, other_fees=0.0)

    def batch_estimate(self, delta_quantities, prices):
        import pandas as pd

        amounts = (delta_quantities.abs() * prices).astype(float)
        commission = amounts * self._commission_rate
        minimum = pd.Series(self._min_commission, index=commission.index)
        commission = pd.concat([commission, minimum], axis=1).max(axis=1)
        return commission


class LocalBundleMod(AbstractMod):
    def __init__(self) -> None:
        self._env = None
        self.cost_decider: HetuStockCostDecider | None = None

    def start_up(self, env, mod_config):
        self._env = env
        bundle_dir = Path(mod_config.bundle_dir)
        data_source = LocalBundleDataSource(bundle_dir)
        env.set_data_source(data_source)
        rules = load_bundle_rules(bundle_dir)
        HetuStockPosition.rules = rules
        HetuStockPosition.action_events = data_source.action_events
        HetuStockPosition.restrictions = []
        HetuStockPosition.terminal_disposals = data_source.terminal_disposals
        HetuStockPosition.disposition_events = []
        simulation_config = env.config.mod.sys_simulation
        # 协议口径：申报量受 participation 上限约束，全天量只做事后成交限制
        # （零成交撤销、涨跌停拒绝）；关闭引擎默认的 25% 每 bar 量限，
        # 避免与协议口径双重约束（低量变体不得改变预先订单成交）。
        simulation_config.volume_limit = False
        env.broker.register_matcher(
            INSTRUMENT_TYPE.CS,
            NextOpenBarMatcher(env, simulation_config, rules),
        )
        self.cost_decider = HetuStockCostDecider(
            env,
            rules,
            commission_rate=float(mod_config.commission_rate),
            min_commission=float(mod_config.min_commission),
            slippage_rate=float(mod_config.slippage_rate),
        )
        env.set_transaction_cost_decider(INSTRUMENT_TYPE.CS, self.cost_decider)
        # 股息差别化个税：fee_rule dividend=holding_diff 时启用（卖出时按持有期扣缴）。
        # sys_accounts 在自身 start_up 时已把配置写到 StockPosition 类属性，
        # 本 Mod 在其后启动，需直接改类属性才生效。
        from rqalpha.mod.rqalpha_mod_sys_accounts.position_model import StockPosition

        StockPosition.dividend_tax_enabled = bool(mod_config.dividend_tax_enabled)

    def tear_down(self, code, exception=None):
        pass


def load_mod():
    return LocalBundleMod()


__config__ = {"priority": 110}
