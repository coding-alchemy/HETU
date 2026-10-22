"""二期 02.1 引擎真实轨迹与独立手算账本一致性。

每个合成语义 case 的期望都在本测试侧直接手算（不调用引擎代码），逐日核对
现金／持仓／可卖／应收／净值／费用：次日开盘成交、T+1、停牌零成交、涨跌停
保守拒绝、整手／零股／科创板数量规则、费率生效切换、容量上限、公司行动
（除息应收／派息到账／送转）、差别化股息税、提高成本实际改变账户。
合成账本不是市场收益证据（02 计划 §1）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import make_backtest_case, write_engine_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "quant" / "engine" / "runner.py"
PHASE2_QUALIFICATION = REPO_ROOT / ".hetu" / "quant" / "qualification" / "phase2"

EL = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _latest_admission_decision() -> str | None:
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    if not records:
        return None
    return str(json.loads(records[-1].read_text(encoding="utf-8")).get("decision"))


def _engine_python() -> Path | None:
    override = os.environ.get("HETU_ENGINE_PYTHON")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None
    default = REPO_ROOT / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    return default if default.exists() else None


ADMITTED = pytest.mark.skipif(
    _latest_admission_decision() != "candidate_ready" or _engine_python() is None,
    reason=(
        f"真实引擎未准入（decision={_latest_admission_decision()!r}）或隔离环境缺失："
        "账户语义正例显式受阻，跳过不构成机制通过证据"
    ),
)


def _run_case(tmp_path: Path, name: str, case: dict) -> Path:
    """落 bundle 并经 runner CLI 走真实引擎；返回输出目录。"""
    bundle = tmp_path / name / "bundle"
    out = tmp_path / name / "engine-out"
    write_engine_bundle(bundle, case)
    completed = subprocess.run(
        [str(_engine_python()), str(RUNNER_PATH), "--bundle", str(bundle), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    return out


def _tables(out: Path) -> dict[str, pd.DataFrame]:
    names = ("signals", "targets", "orders", "trades", "positions", "cash", "nav", "costs")
    return {name: pd.read_parquet(out / f"{name}.parquet") for name in names}


def _cash_on(cash: pd.DataFrame, day: str) -> dict[str, float]:
    row = cash[cash["date"] == day].iloc[0]
    return {
        "cash": float(row["cash_available"]),
        "receivable": float(row["receivable"]),
    }


def _nav_on(nav: pd.DataFrame, day: str) -> float:
    return float(nav[nav["date"] == day].iloc[0]["net_nav"])


@ADMITTED
def test_event_ledger_matches_engine(tmp_path: Path) -> None:
    """手算账本：买 500 股（佣金 5）→ 除息应收 500＋1 拆 2 → 派息到账。

    d1 收盘信号 0.5 权重；d2 开盘 10.00 成交 500 股：成交额 5,000，
    佣金 max(5,000×0.0003)=5，现金 4,995，净值 9,995（费用实际改变账户）。
    d3 每原股派 1 元并 1 拆 2：500→1,000 股、原价 (10−1)/2=4.50、应收 500，
    净值 = 4,995+500+1,000×4.50 = 9,995（无退出信号：期末保留持仓，不强平）。
    d4 派息到账：现金 5,495，净值仍 9,995。
    """
    out = _run_case(tmp_path, "ledger", make_backtest_case("event_ledger"))
    t = _tables(out)

    trades = t["trades"]
    assert len(trades) == 1
    fill = trades.iloc[0]
    assert fill["executed_at"] == EL[1]
    assert fill["code"] == "CODE_A" and fill["side"] == "BUY"
    assert fill["qty"] == 500 and abs(fill["price_raw"] - 10.0) <= 1e-9
    assert abs(fill["commission"] - 5.0) <= 0.01  # max(1.5, 最低 5)
    assert abs(fill["stamp_tax"]) <= 0.01  # 买入无印花税

    cash_d2 = _cash_on(t["cash"], EL[1])
    assert abs(cash_d2["cash"] - 4995.0) <= 0.01  # 10,000 − 5,000 − 5
    assert abs(_nav_on(t["nav"], EL[1]) - 9995.0) <= 0.01

    pos_d3 = t["positions"][t["positions"]["date"] == EL[2]].iloc[0]
    assert pos_d3["qty"] == 1000 and abs(pos_d3["price_raw"] - 4.5) <= 1e-9
    assert pos_d3["sellable_qty"] == 1000  # d2 买入、d3 起可卖
    cash_d3 = _cash_on(t["cash"], EL[2])
    assert abs(cash_d3["receivable"] - 500.0) <= 0.01  # 500 原股 × 每股 1 元
    assert abs(cash_d3["cash"] - 4995.0) <= 0.01  # 除息不到账，现金不变
    assert abs(_nav_on(t["nav"], EL[2]) - 9995.0) <= 0.01  # 4,995+500+4,500

    cash_d4 = _cash_on(t["cash"], EL[3])
    assert abs(cash_d4["cash"] - 5495.0) <= 0.01  # 派息到账
    assert abs(cash_d4["receivable"]) <= 0.01
    pos_d4 = t["positions"][t["positions"]["date"] == EL[3]].iloc[0]
    assert pos_d4["qty"] == 1000  # 未发退出信号：持仓保留
    assert abs(_nav_on(t["nav"], EL[3]) - 9995.0) <= 0.01

    # costs 表与 trades 费用列逐笔一致
    costs = t["costs"]
    assert set(costs["cost_type"]) == {"commission"}
    assert abs(costs["amount"].sum() - 5.0) <= 0.01


@ADMITTED
def test_event_ledger_exit_sell(tmp_path: Path) -> None:
    """手算账本（退出变体）：d4 卖 1,000 股得 4,500，佣金 5、印花税 4.5。

    最终现金 = 5,495 + 4,500 − 5 − 4.5 = 9,985.50；合成税制股息税 0。
    """
    out = _run_case(tmp_path, "exit", make_backtest_case("event_ledger", variant="exit"))
    t = _tables(out)

    sells = t["trades"][t["trades"]["side"] == "SELL"]
    assert len(sells) == 1
    sell = sells.iloc[0]
    assert sell["executed_at"] == EL[3]
    assert sell["qty"] == 1000 and abs(sell["price_raw"] - 4.5) <= 1e-9
    assert abs(sell["commission"] - 5.0) <= 0.01
    assert abs(sell["stamp_tax"] - 4.5) <= 0.01  # 4,500 × 1‰

    final_cash = _cash_on(t["cash"], EL[3])["cash"]
    assert abs(final_cash - 9985.50) <= 0.01
    assert abs(_nav_on(t["nav"], EL[3]) - 9985.50) <= 0.01


@ADMITTED
def test_signal_and_execution_have_separate_clocks(tmp_path: Path) -> None:
    """信号时钟与成交时钟分离：d1 信号不得在 d1 成交；d2 收盘／全天量的
    变体不得改变预先订单数量或 d2 开盘成交决策。"""
    out_base = _run_case(tmp_path, "clocks-base", make_backtest_case("execution_clocks"))
    out_variant = _run_case(
        tmp_path, "clocks-variant", make_backtest_case("execution_clocks", variant="d2_close_volume")
    )
    base, variant = _tables(out_base), _tables(out_variant)

    for t in (base, variant):
        # 信号日 d1 无成交；成交只在 d2 开盘
        assert (t["trades"]["executed_at"] != EL[0]).all()
        fill = t["trades"].iloc[0]
        assert fill["executed_at"] == EL[1]
        assert fill["qty"] == 500 and abs(fill["price_raw"] - 10.0) <= 1e-9

    # d2 收盘／全天量变体不改变订单数量与开盘成交（逐值一致）
    cols = ["executed_at", "code", "side", "qty", "price_raw", "commission"]
    pd.testing.assert_frame_equal(base["trades"][cols], variant["trades"][cols], check_dtype=False)
    # order_id 是引擎进程启动墙钟种子（运行内生成、非语义内容），剔除后比对语义列
    for t in (base, variant):
        assert t["orders"]["order_id"].notna().all()
    order_cols = [c for c in base["orders"].columns if c != "order_id"]
    pd.testing.assert_frame_equal(
        base["orders"][order_cols], variant["orders"][order_cols], check_dtype=False
    )


@ADMITTED
def test_t1_blocks_sell_of_new_shares(tmp_path: Path) -> None:
    """T+1：d2 盘前按权重 1.0 买入 900 股成交后，同盘前再按权重 0.0 清仓，
    新买股当日不可卖被引擎拒绝（orders 记 REJECTED，拒绝原因可查）；
    d4 再给退出信号则正常卖出。卖出未成交不改变持仓与现金。"""
    out = _run_case(tmp_path, "t1", make_backtest_case("execution_clocks", variant="t1_sell_block"))
    t = _tables(out)

    buys = t["trades"][t["trades"]["side"] == "BUY"]
    assert len(buys) == 1 and buys.iloc[0]["qty"] == 900
    assert buys.iloc[0]["executed_at"] == EL[1]

    sell_orders = t["orders"][t["orders"]["side"] == "SELL"]
    assert len(sell_orders) == 2
    rejected = sell_orders[sell_orders["submitted_at"] == EL[1]].iloc[0]
    assert rejected["status"] == "REJECTED" and rejected["filled_qty"] == 0
    filled = sell_orders[sell_orders["submitted_at"] == EL[3]].iloc[0]
    assert filled["status"] == "FILLED"

    sells = t["trades"][t["trades"]["side"] == "SELL"]
    assert len(sells) == 1 and sells.iloc[0]["executed_at"] == EL[3]

    pos_d2 = t["positions"][t["positions"]["date"] == EL[1]].iloc[0]
    assert pos_d2["qty"] == 900 and pos_d2["sellable_qty"] == 0  # 当日买入不可卖
    pos_d3 = t["positions"][t["positions"]["date"] == EL[2]].iloc[0]
    assert pos_d3["sellable_qty"] == 900  # T+1 后转为可卖
    # 现金：d2 买后 995（10,000 − 9,000 − 5）；d4 卖 900 股得 9,000 − 佣金 5 − 印花税 9
    assert abs(_cash_on(t["cash"], EL[1])["cash"] - 995.0) <= 0.01
    assert abs(_cash_on(t["cash"], EL[3])["cash"] - 9981.0) <= 0.01


@ADMITTED
def test_suspended_day_cancels_then_rebalances(tmp_path: Path) -> None:
    """停牌／零成交：d2 全天量 0，d1 信号单当日撤单不成交；d2 收盘新信号
    d3 按真实持仓（仍为零）重算并成交，未成交不变成目标持仓。"""
    out = _run_case(tmp_path, "suspended", make_backtest_case("execution_clocks", variant="suspended"))
    t = _tables(out)

    cancelled = t["orders"][t["orders"]["submitted_at"] == EL[1]]
    assert len(cancelled) == 1 and cancelled.iloc[0]["status"] == "CANCELLED"
    assert (t["trades"]["executed_at"] != EL[1]).all()
    assert abs(_cash_on(t["cash"], EL[1])["cash"] - 10000.0) <= 0.01  # 撤单解冻

    fills = t["trades"]
    assert len(fills) == 1 and fills.iloc[0]["executed_at"] == EL[2]
    assert fills.iloc[0]["qty"] == 500


@ADMITTED
def test_limit_up_blocks_buy_at_open(tmp_path: Path) -> None:
    """涨停不可买：d2 开盘 11.00 = 涨停价（前收 10.00 × pct10），买入保守拒绝。"""
    out = _run_case(tmp_path, "limit-up", make_backtest_case("execution_clocks", variant="limit_up_buy"))
    t = _tables(out)
    assert len(t["trades"]) == 0
    order = t["orders"].iloc[0]
    assert order["status"] == "REJECTED"
    assert "涨停" in order["reason"]
    for day in EL:
        assert abs(_cash_on(t["cash"], day)["cash"] - 10000.0) <= 0.01


@ADMITTED
def test_limit_down_blocks_sell_at_open(tmp_path: Path) -> None:
    """跌停不可卖：d3 开盘 9.00 = 跌停价，卖出保守拒绝，持仓与现金不变。"""
    out = _run_case(tmp_path, "limit-down", make_backtest_case("execution_clocks", variant="limit_down_sell"))
    t = _tables(out)
    sells = t["orders"][t["orders"]["side"] == "SELL"]
    assert len(sells) == 1 and sells.iloc[0]["status"] == "REJECTED"
    assert "跌停" in sells.iloc[0]["reason"]
    assert (t["trades"]["side"] != "SELL").all()
    pos_d3 = t["positions"][t["positions"]["date"] == EL[2]].iloc[0]
    assert pos_d3["qty"] == 500
    assert abs(_cash_on(t["cash"], EL[2])["cash"] - 4995.0) <= 0.01


@ADMITTED
def test_odd_lot_liquidation_sells_once(tmp_path: Path) -> None:
    """零股规则：d2 买 500 股，d3 十送五（m=1.5）→ 750 股零股，d4 清仓
    一次性卖出 750 股（零股余额一次性申报）。"""
    out = _run_case(tmp_path, "odd-lot", make_backtest_case("execution_clocks", variant="odd_lot_sell"))
    t = _tables(out)
    pos_d3 = t["positions"][t["positions"]["date"] == EL[2]].iloc[0]
    assert pos_d3["qty"] == 750
    sells = t["trades"][t["trades"]["side"] == "SELL"]
    assert len(sells) == 1 and sells.iloc[0]["qty"] == 750
    # 现金 = 4,995 + 7,500 − 佣金 5 − 印花税 7.5
    assert abs(_cash_on(t["cash"], EL[3])["cash"] - 12482.5) <= 0.01


@ADMITTED
def test_star_board_qty_rules(tmp_path: Path) -> None:
    """科创板申报规则：200 股起、1 股递增。10,000 元预算（费用预留后
    999.5 股）申报 999 股；同事实主板块规则下只能整手 900 股。"""
    out = _run_case(tmp_path, "star", make_backtest_case("execution_clocks", variant="star_qty"))
    t = _tables(out)
    fill = t["trades"].iloc[0]
    assert fill["qty"] == 999
    assert abs(_cash_on(t["cash"], EL[1])["cash"] - (10000 - 999 * 10 - 5)) <= 0.01


@ADMITTED
def test_chinext_limit_switch_effective_date(tmp_path: Path) -> None:
    """创业板涨跌幅 10%→20%（2020-08-24）：开盘 +12%，生效前拒绝、生效后成交。"""
    before = _tables(_run_case(
        tmp_path, "cx-before", make_backtest_case("execution_clocks", variant="chinext_before")
    ))
    after = _tables(_run_case(
        tmp_path, "cx-after", make_backtest_case("execution_clocks", variant="chinext_after")
    ))
    assert len(before["trades"]) == 0
    assert before["orders"].iloc[0]["status"] == "REJECTED"
    assert len(after["trades"]) == 1
    fill = after["trades"].iloc[0]
    assert fill["qty"] == 400 and abs(fill["price_raw"] - 11.2) <= 1e-9


@ADMITTED
def test_st_limit_switch_effective_date(tmp_path: Path) -> None:
    """主板 ST 涨跌幅 5%→10%（2026-07-06）：开盘 +7%，生效前拒绝、生效后成交。"""
    before = _tables(_run_case(
        tmp_path, "st-before", make_backtest_case("execution_clocks", variant="st_switch_before")
    ))
    after = _tables(_run_case(
        tmp_path, "st-after", make_backtest_case("execution_clocks", variant="st_switch_after")
    ))
    assert len(before["trades"]) == 0
    assert len(after["trades"]) == 1
    fill = after["trades"].iloc[0]
    assert fill["qty"] == 400 and abs(fill["price_raw"] - 10.7) <= 1e-9


@ADMITTED
def test_stamp_tax_period_switch(tmp_path: Path) -> None:
    """印花税 1‰→0.5‰（2023-08-28 生效，按成交日取值）：前变体卖出落在
    2023-08-25（1‰），后变体落在 2023-08-28（0.5‰），过户费双向 0.05 每笔。"""
    before = _tables(_run_case(
        tmp_path, "fee-before", make_backtest_case("execution_clocks", variant="fee_switch_before")
    ))
    after = _tables(_run_case(
        tmp_path, "fee-after", make_backtest_case("execution_clocks", variant="fee_switch_after")
    ))
    sell_day_before = "2023-08-25"
    sell_day_after = "2023-08-29"
    stamp_before = before["trades"][
        (before["trades"]["side"] == "SELL") & (before["trades"]["executed_at"] == sell_day_before)
    ].iloc[0]
    stamp_after = after["trades"][
        (after["trades"]["side"] == "SELL") & (after["trades"]["executed_at"] == sell_day_after)
    ].iloc[0]
    assert abs(stamp_before["stamp_tax"] - 5.0) <= 0.01   # 5,000 × 1‰
    assert abs(stamp_after["stamp_tax"] - 2.5) <= 0.01    # 5,000 × 0.5‰
    for trade in (stamp_before, stamp_after):
        assert abs(trade["transfer_fee"] - 0.05) <= 0.001  # 5,000 × 0.01‰ 双向

    # 现金 = 10,000 − 5,000 − 佣金 5 − 过户 0.05 + 5,000 − 佣金 5 − 印花 − 过户 0.05
    assert abs(_cash_on(before["cash"], sell_day_before)["cash"] - (4994.95 + 5000 - 5 - 5 - 0.05)) <= 0.01
    assert abs(_cash_on(after["cash"], sell_day_after)["cash"] - (4994.95 + 5000 - 5 - 2.5 - 0.05)) <= 0.01


@ADMITTED
def test_capacity_caps_order_quantity(tmp_path: Path) -> None:
    """容量上限：此前 20 日中位量 100,000 × 1% = 1,000 股，现金本可买约
    1,900 股（价 5.00、权重 1.0），申报被截断为 1,000 股。"""
    out = _run_case(tmp_path, "capacity", make_backtest_case("execution_clocks", variant="capacity"))
    t = _tables(out)
    fill = t["trades"].iloc[0]
    assert fill["qty"] == 1000 and abs(fill["price_raw"] - 5.0) <= 1e-9
    assert abs(_cash_on(t["cash"], EL[1])["cash"] - 4995.0) <= 0.01  # 10,000 − 5,000 − 5


@ADMITTED
def test_dividend_tax_withholding_on_sell(tmp_path: Path) -> None:
    """差别化股息税（持有 ≤1 个月 20%）：d2 买 1,000 股，除息每股 0.5 元
    （应收 500），d8 卖出时扣缴 100，现金与费用列逐项可核。"""
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
            "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"]
    out = _run_case(tmp_path, "divtax", make_backtest_case("execution_clocks", variant="dividend_tax"))
    t = _tables(out)

    buy = t["trades"][t["trades"]["side"] == "BUY"].iloc[0]
    assert buy["qty"] == 1000 and abs(buy["price_raw"] - 10.0) <= 1e-9
    assert abs(buy["commission"] - 5.0) <= 0.01
    assert abs(buy["transfer_fee"] - 0.1) <= 0.001  # 10,000 × 0.01‰

    ex_day = days[7]
    cash_ex = _cash_on(t["cash"], ex_day)
    assert abs(cash_ex["receivable"] - 500.0) <= 0.01  # 1,000 × 0.5
    assert abs(cash_ex["cash"] - 1994.90) <= 0.01

    sell_day = days[8]
    sell = t["trades"][t["trades"]["side"] == "SELL"].iloc[0]
    assert sell["executed_at"] == sell_day and sell["qty"] == 1000
    assert abs(sell["dividend_tax"] - 100.0) <= 0.01  # 500 × 20%，持有 1 日
    assert abs(sell["stamp_tax"] - 10.0) <= 0.01      # 10,000 × 1‰

    divtax_costs = t["costs"][t["costs"]["cost_type"] == "dividend_tax"]
    assert len(divtax_costs) == 1 and abs(divtax_costs.iloc[0]["amount"] - 100.0) <= 0.01
    # 现金 = 1,994.90 + 派息 500 + 10,000 − 5 − 10 − 0.1 − 100
    assert abs(_cash_on(t["cash"], sell_day)["cash"] - 12379.80) <= 0.01
    assert abs(_nav_on(t["nav"], sell_day) - 12379.80) <= 0.01


@ADMITTED
def test_raised_costs_actually_change_account(tmp_path: Path) -> None:
    """提高佣金／滑点实际改变账户：同一买入事实（100,000 元、0.5 权重、
    价 10），佣金 0.0006＋滑点 0.001 时费用 80（佣金 30＋滑点 50），
    基准费率时费用 15（佣金 15＋滑点 0），期末现金相差 65。"""
    case = make_backtest_case("execution_clocks", variant="costs_double")
    out_double = _run_case(tmp_path, "costs-double", case)

    base = dict(case)
    base_protocol = dict(base["definition"]["protocol"])  # type: ignore[index]
    base_protocol["costs"] = {
        "commission_rate": 0.0003,
        "min_commission_cny": 5,
        "slippage_rate": 0.0,
        "statutory_rules_ref": "phase2-trading-rules",
    }
    base_definition = dict(base["definition"])  # type: ignore[index]
    base_definition["protocol"] = base_protocol
    base_case = {**base, "definition": base_definition}
    out_base = _run_case(tmp_path, "costs-base", base_case)

    double, baseline = _tables(out_double), _tables(out_base)
    fill_double = double["trades"].iloc[0]
    fill_base = baseline["trades"].iloc[0]
    assert fill_double["qty"] == 5000 and fill_base["qty"] == 5000  # 成交量不变
    assert abs(fill_double["commission"] - 30.03) <= 0.01   # 50,000 × 0.0006
    assert abs(fill_double["slippage_cost"] - 50.0) <= 0.01  # 50,000 × 0.001
    assert abs(fill_base["commission"] - 15.0) <= 0.01     # 50,000 × 0.0003
    assert abs(fill_base["slippage_cost"]) <= 0.01
    cash_double = _cash_on(double["cash"], EL[1])["cash"]
    cash_base = _cash_on(baseline["cash"], EL[1])["cash"]
    assert abs(cash_double - 49919.97) <= 0.01   # 100,000 − 50,000 − 80
    assert abs(cash_base - 49985.0) <= 0.01     # 100,000 − 50,000 − 15
    assert abs(cash_base - cash_double - 65.03) <= 0.01


def _production_action_case(*, delayed=False):
    """明确合成证券/原始行动；实际 provider 标准化后进入同一真实引擎输入包。"""
    from hetu_quant.providers.baostock import BaoStockProvider

    case = make_backtest_case("event_ledger")
    days = EL + (["2024-01-08", "2024-01-09"] if delayed else [])
    for frame in case["tables"].values():
        if "code" in frame:
            frame["code"] = "SYN_A.SZ"
    for signal in case["signals"]:
        signal["code"] = "SYN_A.SZ"
    if delayed:
        for name in ("calendar", "daily_bars", "security_status"):
            frame = case["tables"][name]
            extra = pd.concat([frame.iloc[[-1]].assign(date=day) for day in days[4:]], ignore_index=True)
            case["tables"][name] = pd.concat([frame, extra], ignore_index=True)
    raw = pd.DataFrame([{
        "code": "sz.SYN_A", "dividOperateDate": days[2], "dividRegistDate": days[1],
        "dividPayDate": days[4] if delayed else days[3],
        "dividStockMarketDate": days[5] if delayed else days[2],
        "dividCashPsBeforeTax": "1", "dividStocksPs": "1", "dividReserveToStockPs": "0",
        "dividPreNoticeDate": "2023-12-20", "dividCashStock": "合成10送10派10",
    }])
    case["tables"]["corporate_actions"] = BaoStockProvider().standardize(
        "cn_a_actions", raw, {"tables": ["corporate_actions"]}
    )["corporate_actions"]
    return case


@ADMITTED
def test_production_split_ratio_reaches_real_account(tmp_path):
    case = _production_action_case()
    assert case["tables"]["corporate_actions"]["m"].max() == 2
    t = _tables(_run_case(tmp_path, "production-split", case))
    assert t["positions"].query("date == '2024-01-04'").iloc[0]["qty"] == 1000
    assert _nav_on(t["nav"], EL[2]) == pytest.approx(9995, abs=.01)


@ADMITTED
def test_production_rights_dates_are_distinct(tmp_path):
    case = _production_action_case(delayed=True)
    t = _tables(_run_case(tmp_path, "rights-dates", case))
    pos = t["positions"].set_index("date")
    assert pos.loc[EL[2], "qty"] == 1000
    assert pos.loc[EL[2], "sellable_qty"] == 500
    assert pos.loc['2024-01-09', "sellable_qty"] == 1000
    assert _cash_on(t["cash"], EL[3])["receivable"] == pytest.approx(500)
    assert _cash_on(t["cash"], '2024-01-08')["cash"] == pytest.approx(5495, abs=.01)


@ADMITTED
def test_delisted_position_without_disposal_is_retained(tmp_path):
    case = make_backtest_case("execution_clocks")
    case["tables"]["historical_securities"]["valid_to"] = EL[2]
    t = _tables(_run_case(tmp_path, "delist-no-evidence", case))
    assert _cash_on(t["cash"], EL[3])["cash"] == pytest.approx(4995, abs=.01)
    assert t["positions"].query("date == '2024-01-05'").iloc[0]["qty"] == 500


@ADMITTED
def test_calendar_does_not_extend_execution_interval(tmp_path):
    case = make_backtest_case("event_ledger", "exit")
    case["tables"]["corporate_actions"] = case["tables"]["corporate_actions"].iloc[:0]
    case["definition"]["end"] = EL[1]
    t = _tables(_run_case(tmp_path, "bounded-execution", case))
    assert max(t["nav"]["date"]) == EL[1]
    assert (t["orders"]["submitted_at"] <= EL[1]).all()
    assert (t["trades"]["executed_at"] <= EL[1]).all()


@ADMITTED
def test_slippage_is_execution_price_not_duplicate_cash_fee(tmp_path):
    case = make_backtest_case("execution_clocks", "fee_switch_after")
    case["definition"]["protocol"]["costs"]["slippage_rate"] = .001
    t = _tables(_run_case(tmp_path, "slippage-roundtrip", case))
    for row in t["trades"].itertuples():
        sign = 1 if row.side == "BUY" else -1
        assert row.execution_price == pytest.approx(row.price_raw * (1 + sign * .001))
        assert row.slippage_cost == pytest.approx(row.qty * row.price_raw * .001)
    cash = 10000
    for row in t["trades"].itertuples():
        cash += (-1 if row.side == "BUY" else 1) * row.qty * row.execution_price
        cash -= row.commission + row.stamp_tax + row.transfer_fee + row.dividend_tax
    assert t["cash"].iloc[-1]["cash_available"] == pytest.approx(cash, abs=.01)
    explicit = t["costs"].query("cost_type != 'slippage_cost'")["amount"].sum()
    assert t["nav"].iloc[-1]["gross_nav"] - t["nav"].iloc[-1]["net_nav"] == pytest.approx(explicit, abs=.01)


@ADMITTED
def test_dividend_tax_is_accrued_before_terminal_sale(tmp_path):
    case = make_backtest_case("execution_clocks", "dividend_tax")
    case["signals"] = case["signals"][:1]
    t = _tables(_run_case(tmp_path, "dividend-tax-accrual", case))
    ex = t["cash"].query("date == '2024-01-11'").iloc[0]
    assert ex.dividend_tax_payable == pytest.approx(100, abs=.01)
    pos = t["positions"].query("date == '2024-01-11'")["market_value"].sum()
    assert _nav_on(t["nav"], '2024-01-11') == pytest.approx(
        ex.cash_available + ex.receivable + pos - 100, abs=.01)


@ADMITTED
@pytest.mark.parametrize(("blocked", "exit_price"), [(False, 10), (True, 9), (False, 10.5), (False, 9.5)])
def test_full_monthly_target_exits_omitted_holding(tmp_path, blocked, exit_price):
    case = make_backtest_case("execution_clocks")
    for name, frame in list(case["tables"].items()):
        if "code" in frame and name != "corporate_actions":
            case["tables"][name] = pd.concat([frame, frame.assign(code="SYN_B")], ignore_index=True)
    case["signals"].append({"signal_at": EL[2], "executable_from": EL[3],
                            "code": "SYN_B", "target_weight": .5})
    bars = case["tables"]["daily_bars"]
    mask = (bars["code"] == "CODE_A") & (bars["date"] == EL[3])
    bars.loc[mask, ["open_raw", "close_raw", "high_raw", "low_raw"]] = exit_price
    t = _tables(_run_case(tmp_path, f"omitted-exit-{blocked}-{exit_price}", case))
    exit_orders = t["orders"].query("code == 'CODE_A' and side == 'SELL'")
    assert not exit_orders.empty, "完整组合必须给落选持仓产生退出订单"
    exit_order = exit_orders.iloc[0]
    assert exit_order["status"] == ("REJECTED" if blocked else "FILLED")
    final = t["positions"].query("date == '2024-01-05' and code == 'CODE_A'")
    assert (not final.empty and final.iloc[0]["qty"] == 500) if blocked else final.empty
    assert ((t["targets"]["code"] == "CODE_A") & (t["targets"]["target_weight"] == 0)).any()


@ADMITTED
def test_bought_after_record_date_has_no_dividend_or_bonus(tmp_path):
    case = _production_action_case(delayed=True)
    case["tables"]["corporate_actions"]["record_date"] = EL[0]
    t = _tables(_run_case(tmp_path, "post-record-buy", case))
    assert (t["positions"]["qty"] == 500).all()
    assert (t["cash"]["receivable"] == 0).all()
    assert t["cash"].iloc[-1]["cash_available"] == pytest.approx(4995, abs=.01)


@ADMITTED
def test_missing_required_action_date_is_explicitly_restricted(tmp_path):
    case = _production_action_case()
    case["tables"]["corporate_actions"]["pay_date"] = None
    with pytest.raises(AssertionError, match="corporate_action_dates_unproven"):
        _run_case(tmp_path, "unknown-pay-date", case)


@ADMITTED
def test_dividend_withholding_releases_payable_once(tmp_path):
    case = make_backtest_case("execution_clocks", "dividend_tax")
    t = _tables(_run_case(tmp_path, "tax-payable-withheld", case))
    ex = t["cash"].query("date == '2024-01-11'").iloc[0]
    final = t["cash"].query("date == '2024-01-12'").iloc[0]
    assert ex.dividend_tax_payable == pytest.approx(100, abs=.01)
    assert final.dividend_tax_payable == 0
    assert final.cash_available == pytest.approx(12379.8, abs=.01)
    assert t["trades"]["dividend_tax"].sum() == pytest.approx(100, abs=.01)
    tax_costs = t["costs"].query("cost_type in ['dividend_tax', 'dividend_tax_accrual']")
    assert tax_costs["amount"].sum() == pytest.approx(100)


@ADMITTED
def test_delisting_cash_disposal_requires_original_evidence(tmp_path):
    import hashlib

    case = make_backtest_case("execution_clocks")
    case["tables"]["historical_securities"]["valid_to"] = EL[2]
    original = b'synthetic announced cash compensation: SYN security, 8 CNY per share, 2024-01-04'
    case["tables"]["terminal_disposals"] = pd.DataFrame([{
        "code": "CODE_A", "date": EL[2], "cash_per_share": 8,
        "available_at": '2024-01-02T18:00:00+08:00',
        "evidence_ref": "cash-disposal.txt", "sha256": hashlib.sha256(original).hexdigest(),
    }])
    case["evidence_files"] = {"cash-disposal.txt": original}
    t = _tables(_run_case(tmp_path, "proven-disposal", case))
    assert _cash_on(t["cash"], EL[3])["cash"] == pytest.approx(8995, abs=.01)
    assert t["positions"].query("date == '2024-01-05'").empty
    assert len(t["trades"]) == 1, "具证补偿为公司事件，不伪造卖出订单"


@ADMITTED
def test_dividend_tax_uses_independent_holding_batches(tmp_path):
    from hetu_quant.providers.baostock import BaoStockProvider

    case = _production_action_case()
    days = ['2023-11-01', '2023-11-02', '2024-01-02', '2024-01-03',
            '2024-01-10', '2024-01-11', '2024-01-12']
    for name in ('calendar', 'daily_bars', 'security_status'):
        template = case['tables'][name].iloc[[0]]
        case['tables'][name] = pd.concat([template.assign(date=day) for day in days], ignore_index=True)
    bars = case['tables']['daily_bars']
    bars.loc[bars['date'] >= '2024-01-11', ['open_raw', 'close_raw', 'high_raw', 'low_raw']] = 9
    protocol = case['definition']['protocol']
    protocol['costs'].update(commission_rate=0, min_commission_cny=0)
    case['definition'].update(start=days[0], end=days[-1])
    case['tables']['trading_rules']['fee_rule'] = (
        'stamp=sell:0;transfer=both:0;dividend=holding_diff(<=1m:20%,1m~1y:10%,>1y:0%)')
    case['signals'] = [
        {'signal_at': days[0], 'executable_from': days[1], 'code': 'SYN_A.SZ', 'target_weight': .3},
        {'signal_at': days[2], 'executable_from': days[3], 'code': 'SYN_A.SZ', 'target_weight': .6},
        {'signal_at': days[5], 'executable_from': days[6], 'code': 'SYN_A.SZ', 'target_weight': 0},
    ]
    raw = pd.DataFrame([{'code': 'sz.SYN_A', 'dividOperateDate': days[5], 'dividRegistDate': days[4],
                         'dividPayDate': days[6], 'dividStockMarketDate': '',
                         'dividCashPsBeforeTax': '1', 'dividStocksPs': '0',
                         'dividReserveToStockPs': '0', 'dividPreNoticeDate': '2023-10-20'}])
    case['tables']['corporate_actions'] = BaoStockProvider().standardize(
        'cn_a_actions', raw, {'tables': ['corporate_actions']})['corporate_actions']
    t = _tables(_run_case(tmp_path, 'tax-two-lots', case))
    assert t['cash'].query("date == '2024-01-11'").iloc[0]['dividend_tax_payable'] == pytest.approx(90)
    assert t['trades']['dividend_tax'].sum() == pytest.approx(90)
    assert t['cash'].iloc[-1]['cash_available'] == pytest.approx(9910)


@ADMITTED
@pytest.mark.parametrize(('side', 'open_value', 'rate'), [('BUY', 10.99, .001), ('SELL', 9.01, .002)])
def test_slippage_cannot_fill_beyond_price_limit(tmp_path, side, open_value, rate):
    case = make_backtest_case('execution_clocks')
    case['definition']['protocol']['costs']['slippage_rate'] = rate
    if side == 'SELL':
        case['signals'].append(dict(signal_at=EL[2], executable_from=EL[3], code='CODE_A', target_weight=0))
    day = EL[1] if side == 'BUY' else EL[3]
    bars = case['tables']['daily_bars']
    bars.loc[bars.date == day, ['open_raw', 'close_raw', 'high_raw', 'low_raw']] = open_value
    output = _run_case(tmp_path, f'illegal-slippage-{side}', case)
    tables = _tables(output)
    audit = json.loads((output/'backtest.json').read_text())
    for order in audit['submitted_prices']:
        if order['date'] == day:
            assert 9 <= order['price'] <= 11
            assert order['price']*100 == pytest.approx(round(order['price']*100))
    fills = tables['trades'].query('executed_at == @day and side == @side')
    assert fills.empty, '完整滑点超过限制价时保守拒绝，不截断滑点假成交'
    rejected = tables['orders'].query('submitted_at == @day and side == @side')
    assert (rejected.status == 'REJECTED').all() and not rejected.empty
    assert rejected.reason.str.contains('滑点|价格').all()


@ADMITTED
def test_slippage_prices_obey_tick_and_cash_ledger(tmp_path):
    from decimal import Decimal
    case = make_backtest_case('execution_clocks')
    case['definition']['protocol']['costs']['slippage_rate'] = .0005
    case['signals'].append(dict(signal_at=EL[2], executable_from=EL[3], code='CODE_A', target_weight=0))
    output = _run_case(tmp_path, 'rounded-slippage', case)
    tables = _tables(output)
    assert tables['trades'].execution_price.tolist() == pytest.approx([10.01, 9.99])
    submitted = json.loads((output/'backtest.json').read_text())['submitted_prices']
    assert [order['price'] for order in submitted] == [10.01, 9.99]
    cash = 10000
    for row in tables['trades'].itertuples():
        assert Decimal(str(row.execution_price)) % Decimal('.01') == 0
        assert row.slippage_cost == pytest.approx(abs(row.execution_price-row.price_raw)*row.qty)
        cash += (1 if row.side == 'SELL' else -1)*row.qty*row.execution_price
        cash -= row.commission+row.stamp_tax+row.transfer_fee+row.dividend_tax
    assert tables['cash'].iloc[-1].cash_available == pytest.approx(cash, abs=.01)
    assert tables['nav'].iloc[-1].gross_nav-tables['nav'].iloc[-1].net_nav == pytest.approx(
        tables['costs'].query("cost_type != 'slippage_cost'").amount.sum(), abs=.01)


@ADMITTED
@pytest.mark.parametrize(('side', 'open_value', 'expected'), [('BUY', 10.98, 11), ('SELL', 9.01, 9)])
def test_rounded_slippage_can_fill_at_legal_boundary(tmp_path, side, open_value, expected):
    case = make_backtest_case('execution_clocks')
    case['definition']['protocol']['costs']['slippage_rate'] = .001
    if side == 'SELL':
        case['signals'].append(dict(signal_at=EL[2], executable_from=EL[3], code='CODE_A', target_weight=0))
    day = EL[1] if side == 'BUY' else EL[3]
    bars = case['tables']['daily_bars']
    bars.loc[bars.date == day, ['open_raw', 'close_raw', 'high_raw', 'low_raw']] = open_value
    tables = _tables(_run_case(tmp_path, f'boundary-fill-{side}', case))
    fills = tables['trades'].query('executed_at == @day and side == @side')
    assert len(fills) == 1
    assert fills.iloc[0].execution_price == pytest.approx(expected)


@ADMITTED
def test_slippage_uses_applicable_rule_tick(tmp_path):
    case = make_backtest_case('execution_clocks')
    case['tables']['trading_rules']['tick_size'] = .05  # 明确的合成制度
    case['definition']['protocol']['costs']['slippage_rate'] = .0005
    case['signals'].append(dict(signal_at=EL[2], executable_from=EL[3], code='CODE_A', target_weight=0))
    tables = _tables(_run_case(tmp_path, 'synthetic-tick-005', case))
    assert tables['trades'].execution_price.tolist() == pytest.approx([10.05, 9.95])
    assert tables['trades'].commission.sum() == pytest.approx(10)
    assert tables['trades'].stamp_tax.sum() == pytest.approx(4.975)
    assert tables['cash'].iloc[-1].cash_available == pytest.approx(9935.025, abs=.01)
    assert tables['nav'].iloc[-1].gross_nav == pytest.approx(9950, abs=.01)


@ADMITTED
def test_ex_dividend_open_limit_buy_is_rejected(tmp_path):
    case = make_backtest_case("execution_clocks", "ex_dividend_buy")
    bars = case["tables"]["daily_bars"]
    bars.loc[bars.date == EL[2], ["open_raw", "high_raw", "low_raw", "close_raw"]] = 9.90
    out = _run_case(tmp_path, "ex-dividend-limit-buy", case)
    tables = _tables(out)
    assert tables["trades"].empty
    order = tables["orders"].iloc[0]
    assert order.status == "REJECTED" and "开盘涨停" in order.reason
    assert tables["cash"].iloc[-1].cash_available == 10000
    audit = json.loads((out / "backtest.json").read_text())
    reference = audit["limit_references"][0]
    assert (reference["reference_price"], reference["upper"], reference["lower"]) == pytest.approx((9, 9.9, 8.1))
    assert audit["submitted_prices"][0]["price"] == pytest.approx(9.90)


@ADMITTED
def test_ex_dividend_raw_previous_close_is_not_a_down_limit(tmp_path):
    out = _run_case(tmp_path, "ex-dividend-sell", make_backtest_case("execution_clocks", "ex_dividend_sell"))
    tables = _tables(out)
    assert tables["trades"].side.tolist() == ["BUY", "SELL"]
    sell = tables["trades"].iloc[-1]
    assert sell.executed_at == EL[2] and sell.price_raw == 9 and sell.execution_price == 9
    assert sell.qty == 500
    assert tables["cash"].query("date == @EL[2]").iloc[0].receivable == 500
    assert tables["cash"].iloc[-1].cash_available == pytest.approx(9985.5, abs=.01)
    assert tables["nav"].iloc[-1].net_nav == pytest.approx(9985.5, abs=.01)


@ADMITTED
@pytest.mark.parametrize("variant", ["ex_dividend_unknown_cash", "ex_dividend_missing_previous"])
def test_unproven_limit_reference_rejects_and_limits_account(tmp_path, variant):
    out = _run_case(tmp_path, variant, make_backtest_case("execution_clocks", variant))
    tables = _tables(out)
    assert tables["trades"].empty
    audit = json.loads((out / "backtest.json").read_text())
    assert audit["status"] == "limited"
    assert any("limit_reference_unproven" in reason for reason in audit["limitations"])
    assert tables["orders"].reason.str.contains("limit_reference_unproven").all()
    exclusions = pd.read_parquet(out / "exclusions.parquet")
    assert "limit_reference_price" in exclusions.field.tolist()
    assert tables["cash"].iloc[-1].cash_available == 10000


@ADMITTED
def test_ex_dividend_legal_slippage_keeps_account_prices_raw(tmp_path):
    case = make_backtest_case("execution_clocks", "ex_dividend_buy")
    case["definition"]["protocol"]["costs"]["slippage_rate"] = .001
    bars = case["tables"]["daily_bars"]
    bars.loc[bars.date == EL[2], ["open_raw", "high_raw"]] = 9.81
    out = _run_case(tmp_path, "ex-dividend-slippage", case)
    tables = _tables(out)
    trade = tables["trades"].iloc[0]
    assert trade.price_raw == pytest.approx(9.81) and trade.execution_price == pytest.approx(9.82)
    assert trade.slippage_cost == pytest.approx(5)
    assert tables["cash"].iloc[-1].cash_available == pytest.approx(5085, abs=.01)
    assert tables["positions"].iloc[-1].price_raw == 9
    assert tables["nav"].iloc[-1].gross_nav - tables["nav"].iloc[-1].net_nav == pytest.approx(5, abs=.01)


@ADMITTED
@pytest.mark.parametrize(("with_cash", "opening", "reference"), [(False, 5.5, 5), (True, 4.95, 4.5)])
def test_ex_rights_limit_consumes_production_share_ratio(tmp_path, with_cash, opening, reference):
    case = _production_action_case()
    if not with_cash:
        actions = case["tables"]["corporate_actions"]
        case["tables"]["corporate_actions"] = actions[actions.action_type == "bonus_split"]
    case["signals"] = [dict(signal_at=EL[1], executable_from=EL[2],
                            code="SYN_A.SZ", target_weight=.5)]
    bars = case["tables"]["daily_bars"]
    bars.loc[bars.date == EL[2], ["open_raw", "high_raw", "low_raw", "close_raw"]] = opening
    out = _run_case(tmp_path, f"ex-rights-production-{with_cash}", case)
    assert _tables(out)["trades"].empty
    orders = _tables(out)["orders"]
    assert len(orders) == 1 and "开盘涨停" in orders.iloc[0].reason
    audit = json.loads((out / "backtest.json").read_text())
    assert audit["limit_references"][0]["reference_price"] == pytest.approx(reference)
