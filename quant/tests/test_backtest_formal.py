"""二期 03.1 formal 分支端到端与基准引用（合成数据，真实引擎）。

formal 资格与准入成立时 run_backtest 真实调用 build_signals 产信号（不再
注入 synthetic_signals、不再抛 FormalBacktestNotReadyError），走同一引擎
与发布；资格失败仍 limited 且引擎调用 0。基准先形成可比较轨迹，候选经
resolved_inputs 引用填 nav.benchmark_nav 后评价；超额 = 候选净 CAGR −
基准净 CAGR。真实引擎正例按准入记录门控，跳过不构成通过证据。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import build_case_store as _build_store
from backtest_cases import case_definition as _definition
from backtest_cases import make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant.backtest import read_backtest, run_backtest

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE2_QUALIFICATION = REPO_ROOT / ".hetu" / "quant" / "qualification" / "phase2"
FINAL_WINDOW = ("2023-12-31", "2024-01-31")


def _latest_admission() -> Path | None:
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    return records[-1] if records else None


def _engine_python() -> Path | None:
    default = REPO_ROOT / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    return default if default.exists() else None


READY = pytest.mark.skipif(
    _latest_admission() is None
    or json.loads(_latest_admission().read_text(encoding="utf-8")).get("decision") != "candidate_ready"
    or _engine_python() is None,
    reason=(
        f"真实引擎未准入（见 {PHASE2_QUALIFICATION}）或隔离环境缺失："
        "formal 端到端正例显式受阻，跳过不构成通过证据"
    ),
)


def _qualified_formal(tmp_path: Path, variant: str = "base"):
    case = make_backtest_case("formal", variant=variant)
    store, ref = _build_store(tmp_path / "seed", case)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    return case, store, ref


def _run(case, store, ref, tmp_path: Path, name: str, **kwargs):
    latest = _latest_admission()
    assert latest is not None
    return run_backtest(
        _definition(case, ref.snapshot_id),
        store,
        tmp_path / name,
        admission_path=latest,
        engine_python=_engine_python(),
        **kwargs,
    )


def _expected_vol01_price() -> float:
    """VOL_01（振幅 0.01）在 2024-01-31（月末序列第 39 下标）的合成价。

    value = 100 × 1.01^20 × 0.99^19（下标 1..39 中奇数 20 次、偶数 19 次；
    行情自 2020-10 月末起，共 40 个月末点）。
    """
    return round(100.0 * (1.01 ** 20) * (0.99 ** 19), 2)


@READY
def test_formal_branch_runs_build_signals_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """formal：build_signals 真实调用并落 signals.parquet；发布 complete、九表齐。"""
    case, store, ref = _qualified_formal(tmp_path)
    import hetu_quant.strategies as strategies

    calls: list[int] = []
    real_build = strategies.build_signals

    def spied(definition, store_):
        calls.append(1)
        return real_build(definition, store_)

    monkeypatch.setattr(strategies, "build_signals", spied)
    ref_out = _run(case, store, ref, tmp_path, "out")
    assert calls == [1]  # formal 走真实信号链，不注入 synthetic_signals

    assert ref_out.status == "complete"
    published = read_backtest(ref_out)
    for name in ("signals", "targets", "orders", "trades", "positions", "cash", "nav", "costs", "exclusions"):
        assert name in published["tables"]

    run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    signals = pd.read_parquet(run_dir / "bundle" / "signals.parquet")
    assert len(signals) == 1  # 最低十分位 max(1, floor(11/10)) = 1 只（并列按代码取 VOL_01）
    row = signals.iloc[0]
    assert row["code"] == "VOL_01"
    assert row["signal_at"] == "2023-12-31" and row["executable_from"] == "2024-01-31"

    trades = published["tables"]["trades"]
    assert len(trades) == 1
    fill = trades.iloc[0]
    assert fill["code"] == "VOL_01" and fill["side"] == "BUY"
    assert fill["executed_at"] == "2024-01-31"
    price = _expected_vol01_price()
    assert fill["qty"] == 9900  # min(费用预留后现金、目标市值、容量 1 万股) 取整手
    assert abs(fill["price_raw"] - price) / price <= 1e-6

    manifest = json.loads((run_dir / "backtest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "candidate"
    assert manifest["engine"]["decision"] == "candidate_ready"


@READY
def test_formal_unqualified_inputs_publish_limited_without_engine(tmp_path: Path, monkeypatch) -> None:
    """formal 资格失败：仍 limited、引擎调用 0、build_signals 不调用。"""
    case, store, ref = _qualified_formal(tmp_path)
    csvs = dict(case["csvs"])  # type: ignore[arg-type]
    header = csvs["daily_bars"].splitlines()[0]
    csvs["daily_bars"] = header + "\n"  # 行情事实整体缺失
    case = {**case, "csvs": csvs}
    store2, ref2 = _build_store(tmp_path / "seed2", case)

    engine_calls: list[int] = []
    signal_calls: list[int] = []
    import hetu_quant.strategies as strategies

    monkeypatch.setattr(strategies, "build_signals", lambda *a, **k: signal_calls.append(1))
    monkeypatch.setattr(
        "hetu_quant.backtest.run_engine_subprocess",
        lambda *a, **k: engine_calls.append(1),
    )
    ref_out = run_backtest(
        _definition(case, ref2.snapshot_id),
        store2,
        tmp_path / "out",
        admission_path=_latest_admission(),
        engine_python=_engine_python(),
    )
    assert ref_out.status == "limited"
    assert engine_calls == []
    assert signal_calls == []


@READY
def test_benchmark_reference_fills_nav_and_excess_matches_definition(tmp_path: Path) -> None:
    """基准先行：universe_vw 自身标记 benchmark；候选引用其轨迹填
    benchmark_nav；超额 = 候选净 CAGR − 基准净 CAGR（逐值核对）。"""
    case, store, ref = _qualified_formal(tmp_path)
    bench_case = make_backtest_case("formal")
    bench_definition = dict(bench_case["definition"])  # type: ignore[index]
    bench_definition["strategy_id"] = "universe_vw"
    bench_case = {**bench_case, "definition": bench_definition}

    bench_out = _run(bench_case, store, ref, tmp_path, "bench-out")
    assert bench_out.status == "complete"
    bench_dir = Path(bench_out.root) / "backtests" / bench_out.run_id
    bench_manifest = json.loads((bench_dir / "backtest.json").read_text(encoding="utf-8"))
    assert bench_manifest["kind"] == "benchmark"
    bench_nav = pd.read_parquet(bench_dir / "nav.parquet")

    ref_out = _run(case, store, ref, tmp_path, "cand-out", benchmark=bench_out)
    assert ref_out.status == "complete"
    cand_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    cand_nav = pd.read_parquet(cand_dir / "nav.parquet")

    # benchmark_nav 逐日填入基准轨迹（共享日期完全相等）
    merged = cand_nav.merge(
        bench_nav[["date", "net_nav"]].rename(columns={"net_nav": "bench_fill"}),
        on="date",
        how="left",
    )
    window = merged[(merged["date"] >= FINAL_WINDOW[0]) & (merged["date"] <= FINAL_WINDOW[1])]
    assert len(window) == 1  # actual account starts Jan 2; warmup is not execution
    assert window["benchmark_nav"].tolist() == pytest.approx(window["bench_fill"].tolist())
    manifest = json.loads((cand_dir / "backtest.json").read_text(encoding="utf-8"))
    assert manifest["resolved_inputs"]["benchmark"]["run_id"] == bench_out.run_id

    # 手算：候选 2024-01-31 买 9,900 股 VOL_01@P；formal case 协议费率
    # 佣金 0.0003＋过户 0.00001、滑点 0：费用 = 成交额 × 0.00031。
    # 期末净值 = 初资 − 费用（持仓市值 = 成交额，现金 = 初资 − 成交额 − 费用）
    price = _expected_vol01_price()
    amount = 9900 * price
    expected_final = 1_000_000.0 - amount * 0.00031
    final_row = window[window["date"] == FINAL_WINDOW[1]].iloc[0]
    assert final_row["net_nav"] == pytest.approx(expected_final, abs=0.01)

    evaluation = json.loads((cand_dir / "evaluation.json").read_text(encoding="utf-8"))
    metrics = evaluation["metrics"]
    assert metrics["net_return_window"] == pytest.approx(expected_final / 1e6 - 1.0, abs=1e-9)
    # 超额 = 候选净 CAGR − 基准净 CAGR（年化口径差）
    expected_excess = metrics["net_cagr"] - metrics["benchmark_cagr"]
    assert metrics["excess_cagr"] == pytest.approx(expected_excess, abs=1e-12)
    assert evaluation["economic_status"] in ("not_met", "met", "insufficient_evidence")
    assert evaluation["research_status"] == "evaluated"


@READY
def test_benchmark_reference_rejects_inapplicable_ref(tmp_path: Path) -> None:
    """引用非适用基准（kind 非 benchmark）→ 评价 blocked，不静默放行。"""
    case, store, ref = _qualified_formal(tmp_path)
    # 先跑一个候选（kind=candidate），再把它当基准引用：不可比
    cand_out = _run(case, store, ref, tmp_path, "cand1-out")
    ref_out = _run(case, store, ref, tmp_path, "cand2-out", benchmark=cand_out)
    assert ref_out.status == "complete"
    run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
    evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["research_status"] == "blocked"
    assert evaluation["economic_status"] == "not_evaluated"
    assert any("incompatible_benchmark" in reason for reason in evaluation["reasons"])


@READY
def test_benchmark_ref_reusable_across_candidate_runs(tmp_path: Path) -> None:
    """同一基准引用供多次候选运行复用：不因 run_id/日期不同拒绝可读旧结果。"""
    case, store, ref = _qualified_formal(tmp_path)
    bench_case = make_backtest_case("formal")
    bench_definition = dict(bench_case["definition"])  # type: ignore[index]
    bench_definition["strategy_id"] = "universe_vw"
    bench_out = _run({**bench_case, "definition": bench_definition}, store, ref, tmp_path, "bench-out")

    first = _run(case, store, ref, tmp_path, "cand-a-out", benchmark=bench_out)
    second = _run(case, store, ref, tmp_path / "later", "cand-b-out", benchmark=bench_out)
    assert first.status == "complete" and second.status == "complete"
    for ref_out in (first, second):
        run_dir = Path(ref_out.root) / "backtests" / ref_out.run_id
        evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))
        assert evaluation["research_status"] == "evaluated"


@READY
@pytest.mark.parametrize('difference', ['capital', 'universe', 'costs'])
def test_same_named_benchmark_requires_semantic_comparability(tmp_path, difference):
    import copy

    case, store, ref = _qualified_formal(tmp_path)
    facts = copy.deepcopy(case['definition'])
    facts['strategy_id'] = 'universe_vw'
    if difference == 'capital':
        facts['protocol']['initial_cash_cny'] *= 2
    elif difference == 'universe':
        facts['protocol']['universe'] = {'rule': 'different_synthetic_pool'}
    else:
        facts['protocol']['costs']['commission_rate'] *= 2
    bench = _run({**case, 'definition': facts}, store, ref, tmp_path, 'inapplicable-bench')
    candidate = _run(case, store, ref, tmp_path, 'candidate', benchmark=bench)
    report = json.loads((candidate.root / 'backtests' / candidate.run_id / 'evaluation.json').read_text())
    assert report['research_status'] == 'blocked'
    assert report['economic_status'] == 'not_evaluated'
    assert any('incompatible_benchmark' in text for text in report['reasons'])


@READY
def test_formal_report_consumes_mature_factor_labels(tmp_path):
    case, store, ref = _qualified_formal(tmp_path)
    out = _run(case, store, ref, tmp_path, 'diagnostics')
    run_dir = Path(out.root) / 'backtests' / out.run_id
    report = json.loads((run_dir / 'evaluation.json').read_text())
    diagnostics = report.get('factor_diagnostics')
    assert diagnostics is not None, '正式报告必须消费真实输入生成的标签'
    assert set(diagnostics['horizons']) == {'1', '3', '6'}
    assert diagnostics['horizons']['1']['coverage'] >= 10
    assert diagnostics['horizons']['3']['coverage'] == 0
    labels = pd.read_parquet(run_dir / 'factor-labels.parquet')
    assert (labels['factor_value'] < 0).all()
    assert set(labels['horizon_months']) == {1}
    assert labels['mature_at'].max() <= '2024-01-31'
    assert all(row['reason'] == 'immature_or_cross_split' for row in diagnostics['excluded'])


@READY
def test_formal_diagnostics_all_horizons_are_mature_and_cross_split_isolated(tmp_path):
    case, store, ref = _qualified_formal(tmp_path, variant="diagnostics")
    out = _run(case, store, ref, tmp_path, "full-diagnostics")
    directory = Path(out.root) / "backtests" / out.run_id
    diagnostic = json.loads((directory / "evaluation.json").read_text())["factor_diagnostics"]
    assert all(diagnostic["horizons"][str(h)]["coverage"] >= 10 for h in (1, 3, 6))
    labels = pd.read_parquet(directory / "factor-labels.parquet")
    assert (labels["mature_at"] <= labels["split_end"]).all()
    assert labels["mature_at"].max() <= "2024-12-31"
    assert diagnostic["horizons"]["1"]["n_signal_dates"] > 1
    assert "block_intervals" in diagnostic["horizons"]["1"]
    assert "因子诊断" in (directory / "report.md").read_text()


@READY
def test_pressure_reference_is_main_and_cannot_claim_matched_cost_pass(tmp_path):
    from copy import deepcopy
    case, store, ref = _qualified_formal(tmp_path)
    base = _run(case, store, ref, tmp_path, "main")
    facts = deepcopy(case)
    protocol = facts["definition"]["protocol"]
    protocol["comparison"] = {"purpose": "sensitivity_vs_main", "changed_assumptions": ["costs"]}
    protocol["costs"]["commission_rate"] *= 2
    pressure = _run(facts, store, ref, tmp_path, "pressure", benchmark=base)
    evaluation = json.loads((Path(pressure.root)/"backtests"/pressure.run_id/"evaluation.json").read_text())
    assert evaluation["research_status"] == "evaluated"
    assert evaluation["economic_status"] == "not_evaluated"
    assert evaluation["income_target_met"] is None
    assert evaluation["metrics"]["excess_cagr"] < 0


@READY
def test_formal_labels_do_not_treat_truncated_calendar_as_complete_month(tmp_path):
    case = make_backtest_case('formal', variant='diagnostics')
    case['csvs'] = {name: text.replace('2024-12-31', '2024-12-15')
                    if name in ('calendar', 'daily_bars', 'security_status') else text
                    for name, text in case['csvs'].items()}
    store, ref = _build_store(tmp_path / 'seed', case)
    coverage = case['actions_coverage']
    annotate_actions_coverage(store, ref, coverage['start'], coverage['end'],
                              codes=list(case['codes']), query_basis='ex_date')
    out = _run(case, store, ref, tmp_path, 'truncated-labels')
    directory = Path(out.root)/'backtests'/out.run_id
    labels = pd.read_parquet(directory/'factor-labels.parquet')
    assert '2024-12-15' not in set(labels['mature_at'])


@READY
def test_benchmark_rejects_different_terminal_disposal_facts(tmp_path):
    import shutil

    from hetu_quant.backtest import validate_benchmark_reference

    case, store, ref = _qualified_formal(tmp_path)
    case['definition']['strategy_id'] = 'universe_vw'
    benchmark = _run(case, store, ref, tmp_path, 'benchmark')
    original = Path(benchmark.root)/'backtests'/benchmark.run_id/'bundle'
    candidate = tmp_path/'candidate-input'
    shutil.copytree(original, candidate)
    pd.DataFrame([dict(code='VOL_01', date='2024-01-31', cash_per_share=9,
                       available_at='2024-01-01T00:00:00+08:00', evidence_ref='synthetic', sha256='synthetic')]
                 ).to_parquet(candidate/'tables/terminal_disposals.parquet', index=False)
    with pytest.raises(ValueError, match='incompatible_benchmark.*terminal_disposals'):
        validate_benchmark_reference(_definition(case, ref.snapshot_id), benchmark, candidate)


@READY
@pytest.mark.parametrize('changed_facts', [False, True])
def test_new_snapshot_reference_requires_same_actual_rule_facts(tmp_path, changed_facts):
    from copy import deepcopy
    case, store, ref = _qualified_formal(tmp_path)
    benchmark_case = deepcopy(case)
    benchmark_case['definition']['strategy_id'] = 'universe_vw'
    benchmark = _run(benchmark_case, store, ref, tmp_path, 'old-reference')
    new_case = deepcopy(case)
    if changed_facts:
        new_case['csvs']['trading_rules'] = new_case['csvs']['trading_rules'].replace(
            'transfer=both:0.00001', 'transfer=both:0.00002')
        assert new_case['csvs']['trading_rules'] != case['csvs']['trading_rules']
    new_store, new_ref = _build_store(tmp_path/'new-source', new_case)
    coverage = new_case['actions_coverage']
    annotate_actions_coverage(new_store, new_ref, coverage['start'], coverage['end'],
                              codes=list(new_case['codes']), query_basis='ex_date')
    result = _run(new_case, new_store, new_ref, tmp_path, 'continued', benchmark=benchmark)
    evaluation = json.loads((Path(result.root)/'backtests'/result.run_id/'evaluation.json').read_text())
    assert evaluation['research_status'] == ('blocked' if changed_facts else 'evaluated')
    if changed_facts:
        assert 'data_values:trading_rules' in ' '.join(evaluation['reasons'])


@pytest.mark.parametrize('frequency,expected_days', [
    ('weekly', ['2024-01-05', '2024-01-12', '2024-01-19', '2024-01-26']),
    ('monthly', ['2024-01-31']),
])
def test_transparent_benchmark_mechanism_generates_real_periodic_signals(
    tmp_path, monkeypatch, frequency, expected_days
):
    from backtest_cases import build_prepared_case_store
    from hetu_quant.backtest_reporting import read_backtest as read_report

    case = make_backtest_case('formal', 'weekly_benchmark')
    case['definition']['protocol']['rebalance']['frequency'] = frequency
    store, snapshot = build_prepared_case_store(tmp_path/'facts', case, monkeypatch)
    assert _latest_admission() is not None and _engine_python() is not None
    ref = _run(case, store, snapshot, tmp_path, 'out')
    content = read_report(ref)
    signals = content['tables']['signals']
    assert sorted(signals['signal_at'].unique()) == expected_days
    assert set(signals['code']) == {'VOL_01', 'VOL_02'}
    targets = content['tables']['targets']
    for _, group in targets.groupby('signal_at'):
        assert group.set_index('code')['target_weight'].to_dict() == {'VOL_01': .25, 'VOL_02': .75}
    assert not content['tables']['trades'].empty
    assert content['evaluation']['economic_status'] == 'not_evaluated'
