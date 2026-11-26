"""二期 03.2 机制评价语义：经济判定 ≠ 执行状态，plan-only 不触引擎／信号。

 execute 合成 smoke 走真实引擎（准入门控），预算门禁与基准先行复用均在案。

事实均为手工构造：净值序列、标签成熟度、基准口径与试验台账由本测试侧给出，
期望判定按冻结协议语义手算，不读取生产侧答案。
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pandas as pd
import pytest
from backtest_cases import build_case_store
from hetu_quant.backtest_evaluation import evaluate_backtest, research_exposure_inputs

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
FINAL_WINDOW = ("2024-01-02", "2024-12-31")


def _load(script: str) -> dict:
    return runpy.run_path(str(RESEARCH_DIR / script))


def _protocol(**overrides: object) -> dict:
    protocol: dict[str, object] = {
        "schema_version": 1,
        "stage": "formal",
        "benchmark_id": "universe_vw",
        "time_split": {"final": list(FINAL_WINDOW)},
        "objectives": {
            "income": "final_net_cagr_gt_0_and_excess_vs_universe_vw_gt_0",
            "risk": "ann_vol_lte_90pct_benchmark_and_maxdd_not_worse",
            "tolerance": "zero",
        },
        "trial_budget": {"max_runs": 8},
        "warmup_months": 36,
        "initial_cash_cny": 1_000_000,
        "costs": {
            "commission_rate": 0.0003,
            "min_commission_cny": 5,
            "slippage_rate": 0.0005,
            "statutory_rules_ref": "phase2-trading-rules",
        },
        "rules_ref": "phase2-trading-rules",
        "viewing_record": {"final_seen": False},
    }
    protocol.update(overrides)
    return protocol


def _trading_days() -> list[str]:
    days = pd.bdate_range(FINAL_WINDOW[0], FINAL_WINDOW[1])
    return [str(day.date()) for day in days]


def _nav_frame(net: list[float], gross: list[float], bench: list[float]) -> pd.DataFrame:
    days = _trading_days()
    assert len(net) == len(gross) == len(bench) == len(days)
    return pd.DataFrame(
        {
            "date": days,
            "gross_nav": gross,
            "net_nav": net,
            "benchmark_nav": bench,
        }
    )


def _hand_fact_tables() -> dict[str, pd.DataFrame]:
    """毛 +2%、净 −1%、基准 0%；候选等速阴跌（波动≈0、回撤 1%），基准 ±2% 交替收官持平。

    初资 100 万（协议 initial_cash_cny），净值为实际账户资产。
    """
    days = _trading_days()
    steps = len(days) - 1
    net = [1e6 * (0.99 ** (i / steps)) for i in range(len(days))]
    gross = [1e6 * (1.02 ** (i / steps)) for i in range(len(days))]
    bench = []
    value = 1e6
    for i in range(len(days)):
        bench.append(value)
        value = value * 1.02 if i % 2 == 0 else value / 1.02
    scale = 1e6 / bench[-1]  # 期末归零收益，路径波动不变
    bench = [v * scale for v in bench]
    return {
        "run": pd.DataFrame({"status": ["complete"], "detail": [""]}),
        "nav": _nav_frame(net, gross, bench),
        "benchmark": pd.DataFrame({"benchmark_id": ["universe_vw"], "kind": ["account"]}),
    }


def test_economic_decision_is_not_execution_status() -> None:
    report = evaluate_backtest(_hand_fact_tables(), _protocol())
    assert report["execution_status"] == "complete"
    assert report["research_status"] == "evaluated"
    assert report["income_target_met"] is False  # 净收益 −1% 且超额 −1%，不靠风险通过
    assert report["risk_target_met"] is True
    assert report["economic_status"] == "not_met"
    assert report["research_conclusion"] == "fail"  # 有效负结果，不是未评价

    engine_failed = dict(_hand_fact_tables())
    engine_failed["run"] = pd.DataFrame({"status": ["error"], "detail": ["engine crashed"]})
    failed_report = evaluate_backtest(engine_failed, _protocol())
    assert failed_report["execution_status"] == "execution_failed"
    assert failed_report["economic_status"] == "not_evaluated"
    assert failed_report["income_target_met"] is None

    blocked = dict(_hand_fact_tables())
    blocked["nav"] = blocked["nav"].iloc[0:0]  # 缺关键数据
    blocked_report = evaluate_backtest(blocked, _protocol())
    assert blocked_report["research_status"] == "blocked"
    assert blocked_report["economic_status"] == "not_evaluated"

    mechanism = evaluate_backtest(_hand_fact_tables(), _protocol(stage="mechanism"))
    assert mechanism["economic_status"] == "not_evaluated"
    assert mechanism["research_status"] == "not_evaluated"


def test_evaluation_uses_verified_size_industry_and_market_state() -> None:
    tables = _hand_fact_tables()
    tables["nav"] = pd.DataFrame(
        [("2024-01-02", 1e6, 1e6, 1e6), ("2024-01-31", 1.2e6, 1.2e6, 1.1e6),
         ("2024-02-29", 1.08e6, 1.08e6, 0.99e6)],
        columns=["date", "gross_nav", "net_nav", "benchmark_nav"],
    )
    tables["calendar"] = pd.DataFrame({"date": tables["nav"]["date"], "is_trading_day": True})
    tables["targets"] = pd.DataFrame([
        dict(signal_at="2023-12-29", executable_from="2024-01-02", code=code, target_weight=0.5)
        for code in ("A", "B")
    ])
    tables["positions"] = pd.DataFrame([
        dict(date="2024-01-02", code="A", market_value=400_000),
        dict(date="2024-01-02", code="B", market_value=200_000),
    ])
    tables["exposure_facts"] = pd.DataFrame([
        dict(signal_at="2023-12-29", code="A", market_cap_cny=100e6, market_cap_source="cap-A", industry="I",
             industry_source="member-A"),
        dict(signal_at="2023-12-29", code="B", market_cap_cny=200e6, market_cap_source="cap-B"),
    ])
    result = evaluate_backtest(tables, _protocol())["metrics"]
    observed = result["exposures"]["snapshots"][0]
    assert observed["actual"]["size"]["weighted_mean_market_cap_cny"] == pytest.approx(400e6 / 3)
    assert observed["target"]["size"]["weighted_mean_market_cap_cny"] == 150e6
    assert observed["actual"]["industry"]["weights"] == {"I": pytest.approx(0.4)}
    assert observed["actual"]["industry"]["unknown_weight"] == pytest.approx(0.2)
    assert observed["actual"]["industry"]["status"] == "limited"
    assert result["market_states"]["states"]["up"]["net_return"] == pytest.approx(0.20)
    assert result["market_states"]["states"]["down"]["benchmark_return"] == pytest.approx(-0.10)
    assert result["market_states"]["states"]["up"]["periods"][0]["window"] == ["2024-01-02", "2024-01-31"]


def test_market_state_zero_month_anchor_is_an_evidence_gap() -> None:
    tables = _hand_fact_tables()
    tables["nav"] = pd.DataFrame(
        [("2024-01-02", 1e6, 1e6, 1e6), ("2024-01-31", 0, 0, 1.1e6),
         ("2024-02-29", 0, 0, 1.2e6)],
        columns=["date", "gross_nav", "net_nav", "benchmark_nav"],
    )
    tables["calendar"] = pd.DataFrame({"date": tables["nav"]["date"], "is_trading_day": True})
    states = evaluate_backtest(tables, _protocol())["metrics"]["market_states"]
    assert states["status"] == "limited"
    assert states["states"]["up"]["net_return"] == -1.0
    assert states["gaps"][0]["window"] == ["2024-02-29", "2024-02-29"]


def test_market_states_do_not_use_an_early_mark_as_month_end() -> None:
    tables = _hand_fact_tables()
    tables["nav"] = pd.DataFrame(
        [("2024-01-02", 1e6, 1e6, 1e6), ("2024-01-10", 1.1e6, 1.1e6, 1.1e6),
         ("2024-02-01", 1.15e6, 1.15e6, 1.2e6)],
        columns=["date", "gross_nav", "net_nav", "benchmark_nav"],
    )
    tables["calendar"] = pd.DataFrame(
        {"date": ["2024-01-02", "2024-01-10", "2024-01-31", "2024-02-01", "2024-03-29"], "is_trading_day": True},
    )
    states = evaluate_backtest(tables, _protocol())["metrics"]["market_states"]
    assert not states["states"]
    assert [item["reason"] for item in states["gaps"]] == [
        "缺完整交易日历或月段配对净值", "缺月段开始前的配对净值", "整个月段无配对净值",
    ]


def test_market_month_requires_daily_paired_nav() -> None:
    tables = _hand_fact_tables()
    tables["nav"] = pd.DataFrame([
        ("2024-01-02", 1e6, 1e6, 1e6), ("2024-01-15", 1e6, 1e6, float("nan")),
        ("2024-01-31", 1.05e6, 1.05e6, 1.01e6), ("2024-02-29", 1.1e6, 1.1e6, 1.02e6),
    ], columns=tables["nav"].columns)
    tables["calendar"] = pd.DataFrame({"date": tables["nav"]["date"], "is_trading_day": True})
    report = evaluate_backtest(tables, _protocol())
    assert report["economic_status"] == "insufficient_evidence"
    states = report["metrics"]["market_states"]
    assert [(g["date"], g["field"], g["side"]) for g in states["gaps"][0]["daily"]] == [
        ("2024-01-15", "benchmark_nav", "benchmark"),
    ]
    assert [part["window"] for part in states["states"]["up"]["periods"]] == [["2024-02-29", "2024-02-29"]]


def test_pressure_without_same_pool_series_keeps_main_comparison() -> None:
    tables = _hand_fact_tables()
    report = evaluate_backtest(tables, _protocol(comparison={"purpose": "sensitivity_vs_main"}))
    metrics = report["metrics"]
    assert metrics["comparison"]["reference_trial"] == "final_main"
    assert metrics["comparison"]["reference_return"] == pytest.approx(0)
    states = metrics["market_states"]
    assert states["status"] == "unknown"
    assert states["states"] == {}
    assert states["gaps"][0]["reason"] == "缺同池 universe_vw 账户净值"


def test_exposure_inputs_preserve_historical_classification_and_selected_size(tmp_path) -> None:
    purpose = "backtest_low_volatility"
    signal = "2024-01-31"
    board = dict(board_id="I1", board_kind="industry", name="合成行业", provider="fixture", classification_version="v1")
    member = dict(board_id="I1", observed_at="2024-01-01", available_at="2024-01-01T12:00:00+08:00",
                  effective_from="2024-01-01", effective_to="", membership_kind="historical")
    store, source = build_case_store(tmp_path, {
        "obtained_at": "2024-02-01T18:00:00+08:00",
        "csvs": {
            "boards": pd.DataFrame([board]).to_csv(index=False),
            "memberships": pd.DataFrame([
                dict(member, code="A"), dict(member, code="B", available_at=f"{signal}T18:00:00+08:00"),
                dict(member, code="C", membership_kind="current_snapshot"),
            ]).to_csv(index=False),
        },
        "table_products": {
            "boards": ("test_exposure_boards", "catalog"), "memberships": ("test_exposure_members", "catalog"),
        },
        "permissions": {table: {"allowed_uses": [purpose]} for table in ("boards", "memberships")},
    })
    bundle = tmp_path / "bundle-tables"
    bundle.mkdir()
    pd.DataFrame([
        dict(code="A", date=signal, market_cap_cny=100e6, available_at=f"{signal}T14:00:00+08:00", source_ref="cap-A"),
        dict(code="A", date=signal, market_cap_cny=200e6, available_at=f"{signal}T18:00:00+08:00", source_ref="late-A"),
    ]).to_parquet(bundle / "market_cap.parquet")
    pd.DataFrame().to_parquet(bundle / "knowledge_evidence.parquet")
    targets = pd.DataFrame([dict(signal_at=signal, executable_from="2024-02-01", code=code)
                            for code in ("A", "B", "C")])
    rows = research_exposure_inputs(store, source, purpose, {"targets": targets}, bundle).set_index("code")
    assert rows.at["A", "market_cap_cny"] == 100e6
    assert rows.at["A", "market_cap_source"] == "cap-A"
    assert rows.at["A", "industry"] == "I1"
    assert rows.at["B", "industry"] is None or pd.isna(rows.at["B", "industry"])
    assert rows.at["C", "industry"] is None or pd.isna(rows.at["C", "industry"])


def test_zero_excess_is_not_income_target() -> None:
    tables = _hand_fact_tables()
    days = _trading_days()
    flat = [1e6] * len(days)
    tables["nav"] = _nav_frame(flat, flat, flat)  # 净、基准均 0 收益
    report = evaluate_backtest(tables, _protocol())
    assert report["income_target_met"] is False  # 严格大于 0，零收益不算达到


def test_evaluation_anchor_includes_pre_window_assets() -> None:
    """首日损失不得被窗口首条净值吞掉：初资 100 万 → 首日 80 万 → 期末 99 万 = −1%。"""
    days = ["2024-01-02", "2024-03-28", "2024-06-28", "2024-12-31"]
    net = [0.80e6, 0.85e6, 0.92e6, 0.99e6]
    bench = [1.0e6, 0.95e6, 1.05e6, 1.0e6]  # 高波动、收官持平
    tables = {
        "run": pd.DataFrame({"status": ["complete"], "detail": [""]}),
        "nav": pd.DataFrame(
            {"date": days, "gross_nav": net, "net_nav": net, "benchmark_nav": bench}
        ),
        "benchmark": pd.DataFrame({"benchmark_id": ["universe_vw"], "kind": ["account"]}),
    }
    report = evaluate_backtest(tables, _protocol())
    assert report["metrics"]["net_return_window"] == pytest.approx(-0.01)
    assert report["income_target_met"] is False
    assert report["economic_status"] == "not_met"


def test_evaluation_subwindow_anchors_to_prior_nav() -> None:
    """后续时间切片以区间前最后净值为锚，不能直接套用全局初资。"""
    days = ["2023-12-29", "2024-01-02", "2024-06-28", "2024-12-31"]
    net = [0.50e6, 0.52e6, 0.56e6, 0.60e6]  # 区间内 0.5 → 0.6：+20%
    bench = [0.40e6, 0.42e6, 0.44e6, 0.48e6]  # 基准同幅 +20%
    tables = {
        "run": pd.DataFrame({"status": ["complete"], "detail": [""]}),
        "nav": pd.DataFrame(
            {"date": days, "gross_nav": net, "net_nav": net, "benchmark_nav": bench}
        ),
        "benchmark": pd.DataFrame({"benchmark_id": ["universe_vw"], "kind": ["account"]}),
    }
    protocol = _protocol()
    del protocol["initial_cash_cny"]  # 无全局初资时仍须锚定：用区间前净值
    report = evaluate_backtest(tables, protocol)
    assert report["metrics"]["anchor_date"] == "2023-12-29"
    assert report["metrics"]["net_return_window"] == pytest.approx(0.20)
    # 超额为 0：不达到严格大于零目标
    assert report["income_target_met"] is False


def test_evaluation_without_anchor_is_insufficient_evidence() -> None:
    """既无区间前净值又无协议初资：缺锚点报证据不足，不得按窗口首条净值放行。"""
    tables = _hand_fact_tables()
    protocol = _protocol()
    del protocol["initial_cash_cny"]
    report = evaluate_backtest(tables, protocol)
    assert report["economic_status"] == "insufficient_evidence"
    assert report["income_target_met"] is None
    assert report["research_conclusion"] == "insufficient_evidence"


def test_later_slice_without_boundary_nav_is_insufficient_evidence() -> None:
    """后续评价切片缺边界净值时不得回退全局初资：初资只用于已证明的账户起始段。"""
    days = ["2024-01-02", "2024-06-28", "2024-12-31"]
    net = [1.00e6, 1.10e6, 1.21e6]  # 区间内 +21%（若错用初资当锚将误判收益达标）
    bench = [1.00e6] * 3
    tables = {
        "run": pd.DataFrame({"status": ["complete"], "detail": [""]}),
        "nav": pd.DataFrame(
            {"date": days, "gross_nav": net, "net_nav": net, "benchmark_nav": bench}
        ),
        "benchmark": pd.DataFrame({"benchmark_id": ["universe_vw"], "kind": ["account"]}),
    }
    later_slice = _protocol(
        time_split={
            "development": ["2023-01-03", "2023-12-29"],
            "final": list(FINAL_WINDOW),
        }
    )
    # 后续切片、无边界净值：初资不是切片起点资产，证据不足
    report = evaluate_backtest(tables, later_slice)
    assert report["economic_status"] == "insufficient_evidence"
    assert report["income_target_met"] is None

    # 有边界净值：锚定边界资产（1.5e6 → 1.21e6 实亏），不得改用初资
    with_boundary = dict(tables)
    with_boundary["nav"] = pd.DataFrame(
        {
            "date": ["2023-12-29"] + days,
            "gross_nav": [1.50e6] + net,
            "net_nav": [1.50e6] + net,
            "benchmark_nav": [1.00e6] + bench,
        }
    )
    anchored = evaluate_backtest(with_boundary, later_slice)
    assert anchored["metrics"]["anchor_date"] == "2023-12-29"
    assert anchored["metrics"]["net_return_window"] == pytest.approx(1.21 / 1.50 - 1.0)
    assert anchored["income_target_met"] is False
    assert anchored["economic_status"] == "not_met"

    # 评价区间即账户起始段（协议唯一切片）：协议初资仍是合法锚点
    start_segment = evaluate_backtest(tables, _protocol())
    assert start_segment["income_target_met"] is True  # +21%、基准持平、超额为正


def test_future_labels_and_incompatible_benchmark_rejected() -> None:
    tables = _hand_fact_tables()
    immature = dict(tables)
    immature["labels"] = pd.DataFrame(
        {
            "code": ["VOL_01"],
            "signal_at": ["2024-11-29"],
            "horizon_months": [3],
            "mature_at": ["2025-02-28"],  # 晚于评价区间末 2024-12-31：未成熟
        }
    )
    with pytest.raises(ValueError, match="immature_label"):
        evaluate_backtest(immature, _protocol())

    wrong_basis = dict(tables)
    wrong_basis["benchmark"] = pd.DataFrame(
        {"benchmark_id": ["csi300_price"], "kind": ["price_index"]}
    )
    with pytest.raises(ValueError, match="incompatible_benchmark"):
        evaluate_backtest(wrong_basis, _protocol())

    mismatched_id = dict(tables)
    mismatched_id["benchmark"] = pd.DataFrame(
        {"benchmark_id": ["other_pool"], "kind": ["account"]}
    )
    with pytest.raises(ValueError, match="incompatible_benchmark"):
        evaluate_backtest(mismatched_id, _protocol())


def _write_protocol_file(path: Path, study_id: str = "phase2-lowvol") -> None:
    payload = {
        "schema_version": 1,
        "study_id": study_id,
        "definition": {
            "as_of": "2026-10-17T21:00:00+08:00",
            "snapshot_id": "pending-real-data-admission",
            "start": "2019-11-01",
            "end": "2026-10-13",
            "strategy_id": "low_volatility",
            "parameters": {"lookback_months": 36},
            "protocol": _protocol(universe={"rule": "synthetic_history"},
                execution={"participation_rate": .01}, time_split={
                "development": ["2019-11-01", "2021-12-31"],
                "validation": ["2022-01-01", "2023-12-31"], "final": list(FINAL_WINDOW)}),
            "market": "CN_A",
            "mode": "free",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_all_trials_retained_and_budget_enforced(tmp_path: Path, monkeypatch) -> None:
    ns = _load("run_validation.py")
    protocol_path = tmp_path / "protocol.json"
    _write_protocol_file(protocol_path)
    data_root = tmp_path / "data"
    ledger = data_root / "research" / "phase2-lowvol" / "trials.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [
        {"run_id": f"t{i}", "status": status, "economic_status": eco}
        for i, (status, eco) in enumerate(
            [
                ("complete", "not_met"),  # 负结果保留
                ("complete", "met"),
                ("failed", "not_evaluated"),  # 失败保留
                ("blocked", "not_evaluated"),
                ("complete", "not_met"),
                ("complete", "not_met"),
                ("failed", "not_evaluated"),
                ("complete", "met"),
            ]
        )
    ]
    ledger.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    engine_calls: list[int] = []
    monkeypatch.setitem(
        ns, "_run_engine_once", lambda *a, **k: engine_calls.append(1)
    )
    with pytest.raises(ValueError, match="trial_budget"):
        ns["run_validation"](protocol_path, data_root, tmp_path / "out")
    assert len(engine_calls) == 0  # 第 9 次执行前拒绝

    retained = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [r["run_id"] for r in retained[:8]] == [f"t{i}" for i in range(8)]
    assert retained[8]["status"] == "refused_budget"  # 拒绝也登记，不删旧行


def test_execute_refuses_when_remaining_budget_cannot_cover_runs(
    tmp_path: Path, monkeypatch
) -> None:
    """余量不足本次所需次数时执行前拒绝：台账 2 行、max_runs=3、无可复用基准
    （本次需基准＋候选 2 次），不得登记出第 4 次运行。"""
    ns = _load("run_validation.py")
    protocol_path = tmp_path / "protocol.json"
    _write_protocol_file(protocol_path)
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    payload["definition"]["protocol"]["trial_budget"]["max_runs"] = 3
    protocol_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    data_root = tmp_path / "data"
    ledger = data_root / "research" / "phase2-lowvol" / "trials.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        '\n'.join(
            json.dumps({"run_id": f"t{i}", "kind": "candidate", "status": "complete"})
            for i in range(2)
        )
        + "\n",
        encoding="utf-8",
    )

    engine_calls: list[int] = []
    monkeypatch.setitem(ns, "_run_engine_once", lambda *a, **k: engine_calls.append(1))
    with pytest.raises(ValueError, match="trial_budget"):
        ns["run_validation"](protocol_path, data_root, tmp_path / "out")
    assert len(engine_calls) == 0
    retained = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(retained) == 3 and retained[2]["status"] == "refused_budget"


def test_execute_registers_failed_run_against_budget(
    tmp_path: Path, monkeypatch
) -> None:
    """引擎异常：先登记 failed 行（计入预算）再上抛；失败后余量相应减少。"""
    ns = _load("run_validation.py")
    protocol_path = tmp_path / "protocol.json"
    _write_protocol_file(protocol_path)
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    payload["definition"]["snapshot_id"] = "snap-ok"
    payload["definition"]["protocol"]["trial_budget"]["max_runs"] = 2
    protocol_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / "snapshots" / "snap-ok").mkdir(parents=True)
    (data_root / "qualification" / "phase2").mkdir(parents=True)

    import hetu_quant.backtest as backtest_module
    import hetu_quant.backtest_models as models_module

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("engine exploded")

    # 定义解析不属于运行次数；直接绕过，聚焦引擎异常路径
    monkeypatch.setattr(
        models_module, "backtest_definition_from_dict", lambda payload: payload
    )
    monkeypatch.setattr(backtest_module, "run_backtest", _boom)
    with pytest.raises(RuntimeError, match="engine exploded"):
        ns["run_validation"](protocol_path, data_root, tmp_path / "out")

    ledger = data_root / "research" / "phase2-lowvol" / "trials.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "failed" and rows[0]["kind"] == "benchmark"
    assert "engine exploded" in rows[0]["detail"]

    # failed 计入预算：max_runs=2 中已耗 1 次且无复用基准（本次需 2 次），执行前拒绝
    with pytest.raises(ValueError, match="trial_budget"):
        ns["run_validation"](protocol_path, data_root, tmp_path / "out2")


def test_plan_only_never_runs_strategy(tmp_path: Path, monkeypatch) -> None:
    ns = _load("run_validation.py")
    protocol_path = tmp_path / "protocol.json"
    _write_protocol_file(protocol_path)

    signal_calls: list[int] = []
    engine_calls: list[int] = []
    import hetu_quant.strategies as strategies

    monkeypatch.setattr(
        strategies, "build_signals", lambda *a, **k: signal_calls.append(1)
    )
    monkeypatch.setitem(
        ns, "_run_engine_once", lambda *a, **k: engine_calls.append(1)
    )
    plan = ns["plan_validation"](protocol_path, tmp_path / "data", tmp_path / "out")
    assert len(signal_calls) == 0
    assert len(engine_calls) == 0
    assert plan["planned_runs"] == 8
    assert plan["costs"]["data_cost_cny"] == 0
    assert plan["costs"]["company_research_calls"] == 0
    assert plan["costs"]["llm_business_calls"] == 0


def test_cli_requires_explicit_mode(tmp_path: Path) -> None:
    ns = _load("run_validation.py")
    protocol_path = tmp_path / "protocol.json"
    _write_protocol_file(protocol_path)
    with pytest.raises(SystemExit):
        ns["main"](
            ["--protocol", str(protocol_path), "--data-root", str(tmp_path / "d"),
             "--out", str(tmp_path / "o")]
        )


def test_execute_runs_benchmark_first_then_candidate_and_reuses_it(tmp_path: Path) -> None:
    """合成 smoke（真实引擎，准入门控）：execute 先形成基准轨迹再评价候选；
    台账逐行登记；第二次 execute 复用已批准基准；预算耗尽后执行前拒绝。"""
    import shutil

    from backtest_cases import build_case_store, make_backtest_case
    from fixtures import annotate_actions_coverage

    repo_root = Path(__file__).resolve().parents[2]
    admission = sorted(
        (repo_root / ".hetu" / "quant" / "qualification" / "phase2").glob(
            "engine-admission-*/engine-admission.json"
        )
    )
    engine = repo_root / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    if not admission or not engine.exists():
        pytest.skip("真实引擎未准入或隔离环境缺失：execute smoke 显式受阻")
    latest = admission[-1]
    if json.loads(latest.read_text(encoding="utf-8")).get("decision") != "candidate_ready":
        pytest.skip("真实引擎未准入：execute smoke 显式受阻")

    case = make_backtest_case("formal")
    seed = tmp_path / "seed"
    store, ref = build_case_store(seed, case)
    shutil.copyfile(seed / "products.json", store.root / "data-products.json")
    coverage = case["actions_coverage"]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",  # type: ignore[arg-type]
    )
    data_root = store.root
    admission_dir = data_root / "qualification" / "phase2" / latest.parent.name
    admission_dir.mkdir(parents=True)
    shutil.copyfile(latest, admission_dir / "engine-admission.json")
    # 引擎解释器：包装脚本（venv 经外部符号链接会丢上下文）
    engine_bin = data_root / "runtime" / "rqalpha" / ".venv" / "bin"
    engine_bin.mkdir(parents=True)
    wrapper = engine_bin / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{engine}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)

    definition = dict(case["definition"])  # type: ignore[index]
    definition["snapshot_id"] = ref.snapshot_id
    protocol = dict(definition["protocol"])  # type: ignore[index]
    protocol["trial_budget"] = {"max_runs": 3}
    protocol["time_split"] = {
        "development": ["2024-01-02", "2024-01-15"],
        "validation": ["2024-01-16", "2024-01-31"],
        "final": ["2024-02-01", "2024-03-31"],
    }
    definition["protocol"] = protocol
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(
        json.dumps(
            {"schema_version": 1, "study_id": "synthetic-study", "definition": definition},
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    ns = _load("run_validation.py")
    report = ns["run_validation"](protocol_path, data_root, tmp_path / "out1")
    assert report["mode"] == "execute"
    assert report["execution_status"] == "complete"
    assert report["integration_status"] == "integrated"
    assert report["runs"][0]["kind"] == "benchmark"
    assert report["runs"][1]["kind"] == "candidate"
    assert report["economic_status"] == "not_evaluated"
    assert "final" not in report["definition"]["protocol"]["time_split"]

    ledger = data_root / "research" / "synthetic-study" / "trials.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in rows] == ["benchmark", "candidate"]
    assert all(row["status"] == "complete" for row in rows)

    # 第二次 execute：复用已批准基准（基准运行 0 次新增），只跑候选
    second = ns["run_validation"](protocol_path, data_root, tmp_path / "out2")
    assert len(second["runs"]) == 1
    assert second["benchmark"]["reused"] is True
    assert second["trial_id"] == "dev_neighbor_30"
    assert second["definition"]["protocol"]["warmup_months"] == 30
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in rows] == ["benchmark", "candidate", "candidate"]

    # 预算 3 次用尽：第 4 次执行前拒绝并登记，不删旧行
    with pytest.raises(ValueError, match="trial_budget"):
        ns["run_validation"](protocol_path, data_root, tmp_path / "out3")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[3]["status"] == "refused_budget"
    assert [row["kind"] for row in rows[:3]] == ["benchmark", "candidate", "candidate"]


def test_statistics_ignore_nav_after_evaluation_end():
    tables = _hand_fact_tables()
    first = evaluate_backtest(tables, _protocol())
    future = pd.DataFrame({'date': ['2025-01-31', '2025-02-28', '2025-03-31'],
                           'net_nav': [2e6, 1e4, 5e6], 'gross_nav': [2e6, 1e4, 5e6],
                           'benchmark_nav': [1e6, 1e6, 1e6]})
    changed = dict(tables, nav=pd.concat([tables['nav'], future], ignore_index=True))
    second = evaluate_backtest(changed, _protocol())
    assert first['statistics'] == second['statistics']
    assert first['metrics']['net_return_window'] == second['metrics']['net_return_window']


def test_rename_attribution_carries_pretransfer_value_without_fake_flows():
    from hetu_quant.backtest_evaluation import account_attribution

    positions = pd.DataFrame([
        dict(date="2024-01-01", code="OLD", qty=100, market_value=600),
        dict(date="2024-01-02", code="NEW", qty=100, market_value=660),
    ])
    nav = pd.DataFrame([dict(date="2024-01-01", net_nav=600), dict(date="2024-01-02", net_nav=660)])
    result = account_attribution(
        {"positions": positions, "nav": nav}, pd.DataFrame(), _protocol(),
        identity_events=[dict(from_code="OLD", code="NEW", date="2024-01-02", quantity=100)],
    )
    assert result["by_security"]["OLD"]["observed_pnl_cny"]["final"] == 0
    assert result["by_security"]["NEW"]["observed_pnl_cny"]["final"] == 60
    assert result["by_period"]["final"]["unallocated_cny"] == 0
    assert result["identity_transfers"][0]["transferred_market_value_cny"] == 600


def test_attribution_uses_source_signal_and_actual_fills():
    from hetu_quant import backtest_evaluation

    tables = {
        "signals": pd.DataFrame([{"code": "A", "signal_value": 0.6}]),
        "orders": pd.DataFrame([
            {"code": "A", "status": "FILLED", "reason": "", "requested_qty": 100, "filled_qty": 60},
            {"code": "B", "status": "REJECTED", "reason": "suspended", "requested_qty": 100, "filled_qty": 0},
        ]),
        "costs": pd.DataFrame([
            {"code": "A", "amount": 5, "cost_type": "commission"},
            {"code": "A", "amount": 1, "cost_type": "slippage_cost"},
        ]),
    }
    source = pd.DataFrame([{"code": "A", "signal_at": "2024-01-31", "signal_value": 0.02,
                            "rank": 1, "target_weight": 0.6}])
    attribution = getattr(backtest_evaluation, "account_attribution", None)
    assert attribution is not None
    result = attribution(tables, source, _protocol())
    a, b = result["by_security"]["A"], result["by_security"]["B"]
    assert a["signal_values"] == [0.02]
    assert a["unfilled_qty"] == 40 and a["explicit_fees_cny"] == 5
    assert b["unfilled_pnl_cny"] is None and b["unfilled_reasons"] == ["suspended"]


@pytest.mark.parametrize(("has_action_evidence", "pnl", "unallocated"), [(True, 0, 0), (False, -100, 100)])
def test_cash_dividend_attribution_requires_security_evidence(has_action_evidence, pnl, unallocated):
    from hetu_quant.backtest_evaluation import account_attribution

    tables = {
        "positions": pd.DataFrame([
            {"date": "2024-01-01", "code": "A", "qty": 100, "market_value": 1000},
            {"date": "2024-01-02", "code": "A", "qty": 100, "market_value": 900},
        ]),
        "cash": pd.DataFrame([
            {"date": "2024-01-01", "cash_available": 0, "receivable": 0, "dividend_tax_payable": 0},
            {"date": "2024-01-02", "cash_available": 0, "receivable": 100, "dividend_tax_payable": 0},
        ]),
        "nav": pd.DataFrame([{"date": day, "net_nav": 1000} for day in ("2024-01-01", "2024-01-02")]),
    }
    evidence = [{"code": "A", "date": "2024-01-02", "kind": "cash_dividend",
                 "amount_cny": 100, "entitled_qty": 100, "cash_per_share": 1,
                 "record_date": "2024-01-01", "pay_date": "2024-01-03",
                 "source_ref": "bundle:corporate_actions.parquet"}] if has_action_evidence else []
    result = account_attribution(tables, pd.DataFrame(), _protocol(), cash_action_flows=evidence)
    assert result["by_security"]["A"]["observed_pnl_cny"]["final"] == pnl
    final = result["by_period"]["final"]
    assert final["cash_action_pnl_cny"] == (100 if has_action_evidence else 0)
    assert (final["observed_pnl_cny"], final["unallocated_cny"], final["net_nav_change_cny"]) == (pnl, unallocated, 0)


def test_attribution_traces_actual_holdings_and_orders_by_signal_date():
    from hetu_quant.backtest_evaluation import account_attribution

    targets = pd.DataFrame([
        {"signal_at": "2024-01-31", "executable_from": "2024-02-01", "code": "A", "target_weight": .6},
        {"signal_at": "2024-02-29", "executable_from": "2024-03-01", "code": "A", "target_weight": 0},
    ])
    tables = {
        "targets": targets,
        "nav": pd.DataFrame([{"date": "2024-02-01", "net_nav": 1500}, {"date": "2024-03-01", "net_nav": 1200}]),
        "positions": pd.DataFrame([
            {"date": "2024-02-01", "code": "A", "qty": 60, "market_value": 600},
            {"date": "2024-03-01", "code": "A", "qty": 60, "market_value": 480},
        ]),
        "orders": pd.DataFrame([
            {"signal_at": "2024-01-31", "order_id": "buy-1", "code": "A", "submitted_at": "2024-02-01",
             "requested_qty": 100, "filled_qty": 60, "status": "PARTIAL", "reason": "capacity"},
            {"signal_at": "2024-02-29", "order_id": "", "code": "A", "submitted_at": "2024-03-01",
             "requested_qty": 60, "filled_qty": 0, "status": "REJECTED", "reason": "suspended"},
        ]),
    }
    source = pd.DataFrame([
        {"signal_at": "2024-01-31", "code": "A", "signal_value": .02, "rank": 1, "target_weight": .6},
    ])
    result = account_attribution(tables, source, _protocol())
    before_exit, after_exit = result["by_security"]["A"]["position_deviations"]
    assert before_exit["signal_at"] == "2024-01-31" and before_exit["executable_from"] == "2024-02-01"
    assert before_exit["actual_qty"] == 60 and before_exit["actual_market_value_cny"] == 600
    assert before_exit["actual_weight"] == pytest.approx(.4)
    assert before_exit["weight_deviation"] == pytest.approx(-.2)
    assert before_exit["orders"][0]["order_id"] == "buy-1"
    assert after_exit["signal_at"] == "2024-02-29" and after_exit["executable_from"] == "2024-03-01"
    assert after_exit["target_weight"] == 0 and after_exit["weight_deviation"] == pytest.approx(.4)
    assert after_exit["orders"][0]["reason"] == "suspended"
