"""二期 02.1 正式引擎进程入口：许可门禁在导入引擎前生效，账户由引擎维护。

run_engine 读取已核验的本地 bundle，以 RQAlpha 6.4.1 完全离线执行，
把九张固定轨迹表写到 --out。输入布局以 prepare_backtest 合同为准：
- 根级：definition.json、signals.parquet；admission.json（探查／测试直跑
  的门禁输入，生产经 run_backtest 在暂存目录写入）；
- tables/：calendar、daily_bars、security_status、historical_securities、
  corporate_actions、trading_rules（证券制度与费率仅由此取值）。

执行语义（02.1，均经独立手算账本对照）：
- 信号日收盘信号，executable_from（次交易日）盘前以当日开盘限价报送，
  成交价＝当日开盘价（execution.fill_time=next_open，撮合见 mod.py）；
- 申报量受现金（费用预留）、trading_rules 数量规则（整手／科创板 200 起
  1 股递增、零股一次性卖出）与此前 20 个交易日成交量中位数的 participation
  上限约束；当日未成交部分收盘撤销，下一调仓按真实持仓重算；
- 开盘处于不利限制价保守拒绝（涨停不可买、跌停不可卖）；停牌／零成交
  不成交；全天量只做事后成交限制，不反馈下单决策；
- 佣金（协议费率含最低，按订单一次计收）、印花税（卖出单边）、过户费
  （双边）按 trading_rules fee_rule 逐日期取值；滑点进入execution_price，
  单列归因且不再扣现金；股息差别化个税逐批次计提并在卖出时扣缴；
- 公司行动：登记日权益、除息应收、实际派息日到账、送转上市日可卖；
  日期缺失拒绝执行。退市无处置原件时保留对象与估值限制，不按末价返现。

九张轨迹表的列定义与 quant/src/hetu_quant/backtest.py 的 TABLE_COLUMNS
保持一致（引擎进程独立运行，无法导入 hetu_quant，故常量在本文件维护）。

命令：`<engine_python> quant/engine/runner.py --bundle <目录> --out <暂存目录>`
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

ADMISSION_READY = "candidate_ready"

# 与 hetu_quant.backtest.TABLE_COLUMNS 保持一致（引擎进程独立，不导入 hetu_quant）
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "signals": ("signal_at", "executable_from", "code", "signal_value", "rank", "source_ref"),
    "targets": ("signal_at", "executable_from", "code", "target_weight"),
    "orders": (
        "order_id", "signal_at", "submitted_at", "code", "side",
        "requested_qty", "filled_qty", "status", "reason",
    ),
    "trades": (
        "trade_id", "order_id", "executed_at", "code", "side", "qty",
        "price_raw", "execution_price", "commission", "stamp_tax",
        "transfer_fee", "dividend_tax", "slippage_cost",
    ),
    "positions": ("date", "code", "qty", "sellable_qty", "price_raw", "market_value"),
    "cash": ("date", "cash_available", "receivable", "dividend_tax_payable"),
    "nav": ("date", "gross_nav", "net_nav", "benchmark_nav"),
    "costs": ("date", "order_id", "code", "cost_type", "amount"),
    "exclusions": ("date", "code", "scope", "field", "reason", "source_ref"),
}


class EngineAdmissionError(RuntimeError):
    """准入未通过时拒绝启动引擎。"""


class BundleDataError(RuntimeError):
    """本地输入包缺失时拒绝运行；不允许在线回退。"""


def _load_admission(bundle_dir: Path) -> dict[str, object]:
    admission_path = bundle_dir / "admission.json"
    if not admission_path.exists():
        raise EngineAdmissionError(f"engine admission record missing: {admission_path}")
    return json.loads(admission_path.read_text(encoding="utf-8"))


def run_engine(bundle_dir: Path, output_dir: Path) -> None:
    bundle_dir = Path(bundle_dir)
    output_dir = Path(output_dir)
    admission = _load_admission(bundle_dir)
    if admission.get("decision") != ADMISSION_READY:
        raise EngineAdmissionError(
            f"engine admission decision is {admission.get('decision')!r}, "
            f"required {ADMISSION_READY!r}; refusing to start engine"
        )
    root_required = ("definition.json", "signals.parquet")
    missing = [name for name in root_required if not (bundle_dir / name).exists()]
    tables_required = ("calendar", "daily_bars", "historical_securities", "trading_rules")
    missing += [f"tables/{name}.parquet" for name in tables_required
                if not (bundle_dir / "tables" / f"{name}.parquet").exists()]
    if missing:
        raise BundleDataError(
            f"local bundle inputs missing ({', '.join(missing)}) in {bundle_dir}; "
            "remote fallback is forbidden"
        )
    definition = json.loads((bundle_dir / "definition.json").read_text(encoding="utf-8"))
    protocol = definition.get("protocol") or {}
    initial_cash = float(
        definition.get("initial_cash_cny") or protocol.get("initial_cash_cny") or 1_000_000
    )
    # 成本与执行参数：生产定义在 protocol 下，探查/测试合成入口在顶层（兼容回退）
    costs = definition.get("costs") or protocol.get("costs") or {}
    commission_rate = float(costs.get("commission_rate") or 0.0)
    min_commission = float(costs.get("min_commission_cny") or 0.0)
    slippage_rate = float(costs.get("slippage_rate") or 0.0)
    execution = definition.get("execution") or protocol.get("execution") or {}
    participation_rate = float(execution.get("participation_rate") or 0.01)
    volume_lookback_days = int(execution.get("volume_lookback_days") or 20)
    _execute_with_rqalpha(
        bundle_dir,
        output_dir,
        initial_cash,
        commission_rate=commission_rate,
        min_commission=min_commission,
        slippage_rate=slippage_rate,
        participation_rate=participation_rate,
        volume_lookback_days=volume_lookback_days,
    )


def _round_down(qty: float, step: int) -> int:
    return int(qty) // step * step


def _execute_with_rqalpha(
    bundle_dir: Path,
    output_dir: Path,
    initial_cash: float,
    *,
    commission_rate: float,
    min_commission: float,
    slippage_rate: float,
    participation_rate: float,
    volume_lookback_days: int,
) -> None:
    """以 RQAlpha 6.4.1 完全离线执行 bundle，写九张轨迹表。"""
    import pandas as pd
    import rqalpha
    from rqalpha import api as rq_api
    from rqalpha.core.events import EVENT
    from rqalpha.environment import Environment
    from rqalpha.model.order import LimitOrder

    engine_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("hetu_engine_mod", engine_dir / "mod.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load engine mod from {engine_dir / 'mod.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hetu_engine_mod", module)
    spec.loader.exec_module(module)
    from rules import load_bundle_rules  # 引擎进程旁路导入（quant/engine 在 sys.path）

    rules = load_bundle_rules(bundle_dir)

    signals = pd.read_parquet(bundle_dir / "signals.parquet")
    bars = pd.read_parquet(bundle_dir / "tables" / "daily_bars.parquet")
    calendar_days = [
        str(d)
        for d in pd.read_parquet(bundle_dir / "tables" / "calendar.parquet")["date"].astype(str)
    ]
    definition = json.loads((bundle_dir / "definition.json").read_text())
    execution_start = str(definition.get("start") or calendar_days[0])
    execution_end = str(definition.get("end") or calendar_days[-1])
    execution_days = [day for day in calendar_days if execution_start <= day <= execution_end]
    if not execution_days:
        raise BundleDataError("执行区间无交易日")
    signals = signals[signals["executable_from"].astype(str).between(execution_start, execution_end)]
    day_rank = {day: index for index, day in enumerate(calendar_days)}

    close_price = {(str(r.code), str(r.date)): float(r.close_raw) for r in bars.itertuples()}
    open_price = {(str(r.code), str(r.date)): float(r.open_raw) for r in bars.itertuples()}
    volume_by_day: dict[str, list[tuple[str, float]]] = {}
    for r in bars.itertuples():
        volume_by_day.setdefault(str(r.code), []).append((str(r.date), float(r.volume_shares)))

    # 股息税制：全部证券 holding_diff 启用差别化个税；全部 zero 关闭；混合拒绝
    signal_codes = sorted({str(c) for c in signals["code"].astype(str)}) if len(signals) else []
    modes = set()
    for code in signal_codes:
        for day in {str(d) for d in signals[signals["code"].astype(str) == code]["executable_from"].astype(str)}:
            modes.add(str(rules.fee_components(code, day)["dividend_mode"]))
    if modes == {"holding_diff"}:
        dividend_tax_enabled = True
    elif modes in ({"zero"}, set()):
        dividend_tax_enabled = False
    else:
        raise ValueError(f"股息税制混合不支持: {sorted(modes)}")

    signals_by_exec_day: dict[str, list] = {}
    for row in signals.itertuples():
        signals_by_exec_day.setdefault(str(row.executable_from), []).append(row)

    state: dict[str, list] = {"orders": [], "rejected": [], "attempts": [], "trades": [], "daily": [], "divtaxes": []}

    def init(context) -> None:
        env = Environment.get_instance()
        mod = env.mod_dict["hetu_local_bundle"]
        decider = mod.cost_decider

        def on_order_rejected(event) -> None:
            # 前端校验拒绝（如 T+1 超可卖卖出）：api.order 不返回订单对象，
            # 用最近一笔同代码报送尝试补记 REJECTED 行，原因取自事件。
            code = str(event.order_book_id)
            for attempt in reversed(state["attempts"]):
                if attempt["code"] == code and not attempt["recorded"]:
                    attempt["recorded"] = True
                    state["rejected"].append(
                        {
                            "signal_at": str(attempt["signal"].signal_at),
                            "submitted_at": attempt["submitted_at"],
                            "code": code,
                            "side": attempt["side"],
                            "requested_qty": attempt["qty"],
                            "filled_qty": 0,
                            "status": "REJECTED",
                            "reason": str(event.reason),
                        }
                    )
                    return

        env.event_bus.add_listener(EVENT.ORDER_CREATION_REJECT, on_order_rejected)

        def on_trade(event) -> None:
            trade = event.trade
            commission, stamp, transfer, slippage = decider.breakdown_for(trade.order_id)
            state["trades"].append(
                {
                    "trade_id": str(trade.exec_id),
                    "order_id": str(trade.order_id),
                    "executed_at": trade.trading_datetime.date().isoformat(),
                    "code": str(trade.order_book_id),
                    "side": trade.side.name,
                    "qty": int(trade.last_quantity),
                    "price_raw": open_price[(str(trade.order_book_id), trade.trading_datetime.date().isoformat())],
                    "execution_price": float(trade.last_price),
                    "commission": commission,
                    "stamp_tax": stamp,
                    "transfer_fee": transfer,
                    "dividend_tax": 0.0,
                    "slippage_cost": slippage,
                }
            )

        def on_pay_taxes(event) -> None:
            # 股息差别化个税在卖出成交后扣缴。事件先于本监听器登记顺序到达时，
            # 对应卖出可能尚未入账：先归集，写表时归并到当日该代码的卖出一笔。
            if str(event.tax_type) != "TAX_TYPE.DIVIDEND" and "DIVIDEND" not in str(event.tax_type):
                return
            state["divtaxes"].append(
                {
                    "code": str(event.order_book_id),
                    "day": event.trading_dt.date().isoformat(),
                    "amount": float(event.delta_amount),
                }
            )

        env.event_bus.add_listener(EVENT.TRADE, on_trade)
        env.event_bus.add_listener(EVENT.PAY_TAXES, on_pay_taxes)

    def _capacity_cap(code: str, signal_day: str, step: int) -> int | None:
        """此前 volume_lookback_days 个交易日成交量中位数的 participation 上限。

        无历史成交量记录时返回 None（不设容量上限），不静默按零处理。
        """
        history = [
            v
            for day, v in volume_by_day.get(code, [])
            if day_rank.get(day, 10**9) <= day_rank.get(signal_day, -1)
        ]
        window = history[-volume_lookback_days:]
        if not window:
            return None
        ordered = sorted(window)
        median = (
            ordered[len(ordered) // 2]
            if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
        )
        return _round_down(median * participation_rate, step)

    def before_trading(context) -> None:
        # 信号日收盘信号，executable_from 日盘前报送；current_bar 模式报送时
        # 立即以当日开盘价撮合（见 mod.py）。卖出 proceeds 不得提前用于买入，
        # 故先报送买入（冻结按当时现金），再报送卖出。
        today = context.now.date().isoformat()
        account = next(iter(context.portfolio.accounts.values()))
        todays_targets = list(signals_by_exec_day.get(today, []))
        if todays_targets:
            present = {str(row.code) for row in todays_targets}
            template = todays_targets[0]
            for held in account.positions.values():
                code = str(held.order_book_id)
                if held.quantity > 0 and code not in present:
                    exit_signal = SimpleNamespace(signal_at=str(template.signal_at), executable_from=today,
                                                  code=code, target_weight=0.0)
                    todays_targets.append(exit_signal)
                    state.setdefault("implicit_targets", []).append(vars(exit_signal))
        for signal in todays_targets:
            code = str(signal.code)
            signal_day = str(signal.signal_at)
            price = open_price.get((code, today))
            if price is None or price <= 0:
                continue
            min_buy_qty, step = rules.qty_rules(code, today)
            fees = rules.fee_components(code, today)
            buy_rate = commission_rate + float(fees["transfer_rate"]) + slippage_rate
            capacity = _capacity_cap(code, signal_day, step)
            target_value = float(signal.target_weight) * float(context.portfolio.total_value)
            current_qty = 0
            current_value = 0.0
            held = None
            for candidate in account.positions.values():
                if str(candidate.order_book_id) == code and candidate.quantity != 0:
                    held = candidate
                    current_qty = int(candidate.quantity)
                    current_value = float(candidate.market_value)
            delta_value = target_value - current_value
            if delta_value > 0:
                cash_budget = float(account.cash) + float(account.frozen_cash)
                # 费用预留：佣金（含最低）＋过户费＋滑点按最坏口径从现金中扣除
                affordable = (cash_budget - min_commission) / (price * (1 + buy_rate))
                qty = min(_round_down(affordable, step), _round_down(delta_value / price, step))
                if capacity is not None:
                    qty = min(qty, capacity)
                if qty < min_buy_qty:
                    continue
            else:
                if held is None:
                    continue
                # 不按期前可卖量截断：超可卖的卖出由引擎 PositionValidator 拒绝
                # （T+1 当日买入不可卖的证据），拒绝原因落 orders 表；零股规则：
                # 卖出须为 100 整数倍，或一次性卖出全部（含零股余额）。
                desired = current_qty if float(signal.target_weight) == 0 else -delta_value / price
                liquidate = desired >= current_qty > 0
                qty = _round_down(desired, 100)
                if qty != current_qty and qty % 100 != 0:
                    qty = _round_down(qty, 100)
                if liquidate:
                    qty = current_qty  # 清仓：一次性卖出全部零股
                if capacity is not None:
                    qty = min(qty, capacity)
                if qty <= 0:
                    continue
            side = "BUY" if delta_value > 0 else "SELL"
            state["attempts"].append(
                {
                    "signal": signal,
                    "code": code,
                    "side": side,
                    "qty": qty,
                    "submitted_at": today,
                    "recorded": False,
                }
            )
            limit = rules.slippage_price(code, today, price, slippage_rate, buy=side == "BUY")
            env = Environment.get_instance()
            previous = float(env.data_proxy.get_prev_close(code, env.trading_dt))
            if not math.isfinite(previous) or previous <= 0:
                attempt = state["attempts"][-1]
                attempt["recorded"] = True
                state["rejected"].append(dict(signal_at=signal_day, submitted_at=today, code=code,
                    side=side, requested_qty=qty, reason="缺前收盘，无法形成合法申报价"))
                continue
            upper, lower = rules.limit_price(previous, rules.limit_pct(code, today), rules.tick_size(code, today))
            # 申报价限定为合法界内的步长点；Matcher仍校验完整滑点，不截断后假成交。
            limit = min(upper, max(lower, limit))
            order = rq_api.order(code, qty if side == "BUY" else -qty, style=LimitOrder(limit))
            if not order:
                continue
            for placed in (order if isinstance(order, list) else [order]):
                state["orders"].append(
                    {"signal": signal, "order": placed, "submitted_at": today}
                )

    def after_trading(context) -> None:
        today = context.now.date().isoformat()
        account = next(iter(context.portfolio.accounts.values()))
        cash_total = float(account.cash) + float(account.frozen_cash)
        positions = []
        receivable = 0.0
        tax_payable = 0.0
        for position in account.positions.values():
            long_position = getattr(position, "_long", position)
            receivable += float(getattr(long_position, "dividend_receivable", 0.0))
            tax_payable += float(getattr(long_position, "dividend_tax_payable", 0.0))
            if position.quantity == 0:
                continue
            price = close_price.get((str(position.order_book_id), today), float(position.last_price))
            positions.append(
                {
                    "date": today,
                    "code": str(position.order_book_id),
                    "qty": int(position.quantity),
                    "sellable_qty": int(position.sellable),
                    "price_raw": price,
                    "market_value": int(position.quantity) * price,
                }
            )
        state["daily"].append(
            {"date": today, "cash": cash_total, "receivable": receivable,
             "tax_payable": tax_payable, "positions": positions}
        )

    config = {
        "base": {
            "start_date": execution_days[0],
            "end_date": execution_days[-1],
            "accounts": {"stock": initial_cash},
            "frequency": "1d",
            "data_bundle_path": str(bundle_dir),
            "run_type": "b",
            "market": "CN",
        },
        "extra": {"log_level": "error", "enable_profiler": False, "context_vars": None},
        "mod": {
            "sys_analyser": {"enabled": False},
            "sys_progress": {"enabled": False},
            "hetu_local_bundle": {
                "enabled": True,
                "lib": "hetu_engine_mod",
                "bundle_dir": str(bundle_dir),
                "commission_rate": commission_rate,
                "min_commission": min_commission,
                "slippage_rate": slippage_rate,
                "dividend_tax_enabled": dividend_tax_enabled,
            },
            "sys_simulation": {"price_limit": False, "matching_type": "current_bar"},
        },
    }
    rqalpha.run_func(config=config, init=init, before_trading=before_trading, after_trading=after_trading)
    from position import HetuStockPosition

    state["restrictions"] = HetuStockPosition.restrictions
    state["disposition_events"] = HetuStockPosition.disposition_events
    if state.get("implicit_targets"):
        signals = pd.concat([signals, pd.DataFrame(state["implicit_targets"])], ignore_index=True)
    _write_outputs(
        output_dir,
        state,
        signals,
        execution_days,
        initial_cash,
        rqalpha.__version__,
        bundle_dir,
    )


def _write_outputs(
    output_dir: Path,
    state: dict[str, list],
    signals,
    trading_days: list[str],
    initial_cash: float,
    engine_version: str,
    bundle_dir: Path,
) -> None:
    import pandas as pd

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    signal_rows = []
    targets_rows = []
    for _day, group in signals.groupby("signal_at", sort=False):
        for rank, row in enumerate(group.itertuples(), start=1):
            signal_rows.append(
                {
                    "signal_at": str(row.signal_at),
                    "executable_from": str(row.executable_from),
                    "code": str(row.code),
                    "signal_value": float(row.target_weight),
                    "rank": rank,
                    "source_ref": "bundle:signals.parquet",
                }
            )
            targets_rows.append(
                {
                    "signal_at": str(row.signal_at),
                    "executable_from": str(row.executable_from),
                    "code": str(row.code),
                    "target_weight": float(row.target_weight),
                }
            )

    order_rows = []
    for record in state["orders"]:
        order = record["order"]
        order_rows.append(
            {
                "order_id": str(order.order_id),
                "signal_at": str(record["signal"].signal_at),
                "submitted_at": record["submitted_at"],
                "code": str(record["signal"].code),
                "side": order.side.name,
                "requested_qty": int(order.quantity),
                "filled_qty": int(order.filled_quantity),
                "status": order.status.name,
                "reason": str(getattr(order, "message", "") or ""),
            }
        )
    for record in state.get("rejected", []):
        order_rows.append(
            {
                "order_id": "",
                "signal_at": record["signal_at"],
                "submitted_at": record["submitted_at"],
                "code": record["code"],
                "side": record["side"],
                "requested_qty": int(record["requested_qty"]),
                "filled_qty": 0,
                "status": "REJECTED",
                "reason": record["reason"],
            }
        )

    # 股息差别化个税归并到当日该代码的卖出一笔（trades.dividend_tax 与 costs 一致）；
    # 找不到对应卖出一笔时保留独立行，不丢费用。
    for divtax in state.get("divtaxes", []):
        target = next(
            (
                trade
                for trade in state["trades"]
                if trade["code"] == divtax["code"]
                and trade["executed_at"] == divtax["day"]
                and trade["side"] == "SELL"
            ),
            None,
        )
        if target is not None:
            target["dividend_tax"] += divtax["amount"]
        else:
            state["trades"].append(
                {
                    "trade_id": f"divtax-{divtax['code']}-{divtax['day']}",
                    "order_id": "",
                    "executed_at": divtax["day"],
                    "code": divtax["code"],
                    "side": "SELL",
                    "qty": 0,
                    "price_raw": 0.0,
                    "execution_price": 0.0,
                    "commission": 0.0,
                    "stamp_tax": 0.0,
                    "transfer_fee": 0.0,
                    "dividend_tax": divtax["amount"],
                    "slippage_cost": 0.0,
                }
            )

    cost_rows = []
    for trade in state["trades"]:
        for cost_type, key in (
            ("commission", "commission"),
            ("stamp_tax", "stamp_tax"),
            ("transfer_fee", "transfer_fee"),
            ("dividend_tax", "dividend_tax"),
            ("slippage_cost", "slippage_cost"),
        ):
            amount = float(trade[key])
            if amount:
                cost_rows.append(
                    {
                        "date": trade["executed_at"],
                        "order_id": trade["order_id"],
                        "code": trade["code"],
                        "cost_type": cost_type,
                        "amount": amount,
                    }
                )

    prior_payable = 0.0
    for daily in state["daily"]:
        delta = daily["tax_payable"] - prior_payable
        if delta:
            cost_rows.append({"date": daily["date"], "order_id": "", "code": "",
                              "cost_type": "dividend_tax_accrual", "amount": delta})
        prior_payable = daily["tax_payable"]

    tables = {
        "signals": pd.DataFrame(signal_rows, columns=TABLE_COLUMNS["signals"]),
        "targets": pd.DataFrame(targets_rows, columns=TABLE_COLUMNS["targets"]),
        "orders": pd.DataFrame(order_rows, columns=TABLE_COLUMNS["orders"]),
        "trades": pd.DataFrame(state["trades"], columns=TABLE_COLUMNS["trades"]),
        "positions": pd.DataFrame(
            [row for day in state["daily"] for row in day["positions"]],
            columns=TABLE_COLUMNS["positions"],
        ),
        "exclusions": pd.DataFrame([
            {"date": day, "code": code, "scope": "account_valuation", "field": "disposal",
             "reason": reason, "source_ref": "bundle:historical_securities"}
            for day, code, reason in state.get("restrictions", [])
        ], columns=TABLE_COLUMNS["exclusions"]),
        "costs": pd.DataFrame(cost_rows, columns=TABLE_COLUMNS["costs"]),
    }

    cash_rows = []
    nav_rows = []
    cumulative_costs = 0.0
    trades_by_day: dict[str, list] = {}
    for trade in state["trades"]:
        trades_by_day.setdefault(trade["executed_at"], []).append(trade)
    for day in state["daily"]:
        market_value = sum(row["market_value"] for row in day["positions"])
        net_nav = day["cash"] + day["receivable"] + market_value - day["tax_payable"]
        trade_costs = sum(
            trade["commission"] + trade["stamp_tax"] + trade["transfer_fee"]
            + trade["dividend_tax"]
            for trade in trades_by_day.get(day["date"], [])
        )
        cumulative_costs += trade_costs
        cash_rows.append(
            {
                "date": day["date"],
                "cash_available": day["cash"],
                "receivable": day["receivable"],
                "dividend_tax_payable": day["tax_payable"],
            }
        )
        nav_rows.append(
            {
                "date": day["date"],
                "gross_nav": net_nav + cumulative_costs + day["tax_payable"],
                "net_nav": net_nav,
                "benchmark_nav": float("nan"),
            }
        )
    tables["cash"] = pd.DataFrame(cash_rows, columns=TABLE_COLUMNS["cash"])
    tables["nav"] = pd.DataFrame(nav_rows, columns=TABLE_COLUMNS["nav"])

    for name, frame in tables.items():
        frame.to_parquet(output_dir / f"{name}.parquet", index=False)
    manifest = {
        "runner": "hetu-quant-engine-runner",
        "engine": "rqalpha",
        "engine_version": engine_version,
        "bundle_dir": str(bundle_dir),
        "initial_cash_cny": initial_cash,
        "trading_days": trading_days,
        "status": "limited" if state.get("restrictions") else "complete",
        "limitations": [reason for _day, _code, reason in state.get("restrictions", [])],
        "disposition_events": state.get("disposition_events", []),
        "submitted_prices": [dict(code=str(record["order"].order_book_id),
                                  date=record["submitted_at"], price=float(record["order"].price))
                             for record in state["orders"]],
    }
    (output_dir / "backtest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HETU 二期正式引擎进程入口")
    parser.add_argument("--bundle", required=True, help="已核验的本地输入包目录")
    parser.add_argument("--out", required=True, help="输出暂存目录")
    args = parser.parse_args(argv)
    run_engine(Path(args.bundle), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
