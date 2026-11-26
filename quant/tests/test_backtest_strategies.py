"""二期 03.1 共同信号计算测试：可手算月收益核对原低波动规则。

事实来自 backtest_cases 的 history/signal_pool 变体（±振幅交替月收益、
边界并列、35 月、缺月、未来市值）；期望值按规则手算留在本测试侧。
"""

from __future__ import annotations

import math
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from backtest_cases import build_case_store, case_definition, make_backtest_case
from fixtures import annotate_actions_coverage
from hetu_quant.strategies import build_signals, low_volatility_weights

# 36 个 ±1% 交替月收益的样本标准差（ddof=1）：mean=0，36a²/35 开方
EXPECTED_VOL_1PCT = 0.01 * math.sqrt(36 / 35)


def _pure_returns(amplitudes: dict[str, float], months: int = 36) -> pd.DataFrame:
    rows = []
    for code, amplitude in amplitudes.items():
        for t in range(months):
            rows.append(
                {
                    "code": code,
                    "month_end": f"m{t:02d}",
                    "month_return": amplitude if t % 2 == 0 else -amplitude,
                }
            )
    return pd.DataFrame(rows)


def _caps(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": codes, "market_cap_cny": [1e9 * (i + 1) for i in range(len(codes))]}
    )


def test_low_volatility_keeps_original_signal_rules() -> None:
    amplitudes = {
        "VOL_01": 0.01,
        "VOL_02": 0.01,  # 边界并列
        **{f"VOL_{k:02d}": 0.01 * (k - 1) for k in range(3, 13)},
    }
    codes = sorted(amplitudes)
    out = low_volatility_weights(_pure_returns(amplitudes), _caps(codes), lookback_months=36)
    # N=12 → max(1, floor(12/10))=1；并列按代码稳定截取
    assert out["code"].tolist() == ["VOL_01"]
    assert abs(out["signal_value"].iloc[0] - EXPECTED_VOL_1PCT) <= 1e-12
    assert abs(out["target_weight"].sum() - 1.0) <= 1e-10


def test_low_volatility_weights_rejects_short_window_and_unknown_cap() -> None:
    amplitudes = {f"VOL_{k:02d}": 0.01 * k for k in range(1, 11)}  # 10 个全窗口
    frame = pd.concat([_pure_returns(amplitudes), _pure_returns({"SHORT_35": 0.005}, months=35)])
    codes = sorted(amplitudes)
    out = low_volatility_weights(frame, _caps(codes), lookback_months=36)
    assert "SHORT_35" not in set(out["code"])  # 35 个月收益不得产合格信号
    assert out["code"].tolist() == ["VOL_01"]  # max(1, floor(10/10))

    # 入选但市值未知：不产生权重
    no_cap = low_volatility_weights(
        _pure_returns(amplitudes), _caps(codes[1:]), lookback_months=36
    )
    assert "VOL_01" not in set(no_cap["code"])

    # 合格池不足 10：不产合格信号
    small = {f"C{k}": 0.01 * k for k in range(1, 6)}
    small_out = low_volatility_weights(_pure_returns(small), _caps(sorted(small)))
    assert len(small_out) == 0


def test_build_signals_uses_historical_universe(tmp_path: Path) -> None:
    case = make_backtest_case("history", variant="signal_pool")
    store, ref = build_case_store(tmp_path, case)
    coverage = case["actions_coverage"]  # type: ignore[index]
    annotate_actions_coverage(
        store, ref, coverage["start"], coverage["end"],
        codes=list(case["codes"]), query_basis="ex_date",
    )
    signals = build_signals(case_definition(case, ref.snapshot_id), store)

    last = signals[signals["signal_at"] == "2024-01-31"]
    assert last["code"].tolist() == ["VOL_01"]
    row = last.iloc[0]
    assert abs(float(row["signal_value"]) - EXPECTED_VOL_1PCT) <= 1e-9
    assert abs(float(last["target_weight"].sum()) - 1.0) <= 1e-10
    assert row["executable_from"] == "2024-02-01"  # 收盘信号次日才可执行
    assert int(row["rank"]) == 1
    assert row["source_ref"] == ref.snapshot_id

    # 35 月、缺月、未来市值均不得产合格信号
    for code in ("SHORT_35", "GAP_MONTH", "FUTURE_MCAP"):
        assert code not in set(signals["code"])

    # 可交易账户基准：同一历史合格池、市值权重、同月调仓
    vw = build_signals(
        case_definition(case, ref.snapshot_id, strategy_id="universe_vw"), store
    )
    vw_last = vw[vw["signal_at"] == "2024-01-31"]
    assert "VOL_01" in set(vw_last["code"])
    assert "FUTURE_MCAP" not in set(vw_last["code"])  # 市值当时不可知
    assert abs(float(vw_last["target_weight"].sum()) - 1.0) <= 1e-10

    # 未登记策略（E/P 未接入）拒绝
    with pytest.raises(ValueError, match="strategy_id"):
        build_signals(case_definition(case, ref.snapshot_id, strategy_id="ep_value"), store)


def test_market_cap_uses_signal_time_known_version() -> None:
    """1 月 31 日信号只能用当时已披露市值：2 月 1 日才公开的 999 不得取代已知的 100。"""
    from hetu_quant.strategies import _known_market_cap

    caps = pd.DataFrame(
        [
            {"code": "X", "date": "2024-01-31", "market_cap_cny": 100.0,
             "available_at": "2024-01-30T18:00:00+08:00"},
            {"code": "X", "date": "2024-01-31", "market_cap_cny": 999.0,
             "available_at": "2024-02-01T09:00:00+08:00"},
        ]
    )
    assert _known_market_cap(caps, "X", "2024-01-31") == 100.0


def test_month_end_signals_only_use_facts_through_the_close(tmp_path: Path) -> None:
    def calculate(variant):
        case = make_backtest_case("history", variant="signal_pool")
        if variant == "future":
            case["csvs"]["daily_bars"] += "2024-02-01,VOL_01,300,300,300,300,300,300,1000,300000\n"
            case["csvs"]["corporate_actions"] += (
                "VOL_01,2024-02-01,rights_issue,1,0,synthetic,2024-02-01T09:00:00+08:00\n"
            )
        if variant == "changed_close":
            bars = pd.read_csv(StringIO(case["csvs"]["daily_bars"]))
            affected = bars["date"].eq("2024-01-31") & bars["code"].eq("VOL_01")
            raw = ["open_raw", "high_raw", "low_raw", "close_raw"]
            bars.loc[affected, raw] *= 1.5
            case["csvs"]["daily_bars"] = bars.to_csv(index=False)
        store, ref = build_case_store(tmp_path / variant, case)
        coverage = case["actions_coverage"]
        annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                                  codes=list(case["codes"]), query_basis="ex_date")
        signals = build_signals(case_definition(case, ref.snapshot_id), store)
        return signals.loc[signals["signal_at"].eq("2024-01-31")].drop(columns="source_ref").reset_index(drop=True)

    original = calculate("original")
    assert original["code"].tolist() == ["VOL_01"]
    assert original["signal_value"].item() == pytest.approx(EXPECTED_VOL_1PCT)
    assert original["target_weight"].item() == pytest.approx(1.0)
    pd.testing.assert_frame_equal(original, calculate("future"))
    assert calculate("changed_close")["code"].tolist() == ["VOL_02"]


def test_action_coverage_after_a_signal_does_not_remove_that_signal(tmp_path: Path) -> None:
    case = make_backtest_case("history", variant="signal_pool")
    store, ref = build_case_store(tmp_path, case)
    annotate_actions_coverage(store, ref, case["actions_coverage"]["start"], "2023-12-31",
                              codes=list(case["codes"]), query_basis="ex_date")
    signals = build_signals(case_definition(case, ref.snapshot_id), store)
    assert signals[["signal_at", "code"]].values.tolist() == [["2023-12-31", "VOL_01"]]


@pytest.mark.parametrize("published_at,amount,expected", [
    ("2024-01-31T14:55:00+08:00", "50", ["VOL_02"]),
    ("2024-01-31T18:00:00+08:00", "50", []),
    ("2024-01-31T14:55:00+08:00", "", []),
    ("2024-01-31", "50", []),
], ids=["known-at-close", "disclosed-after-close", "amount-unproved", "same-day-without-time"])
def test_dividend_terms_must_be_proved_at_the_signal_close(tmp_path: Path, published_at, amount, expected) -> None:
    from hetu_quant.backtest_data import prepare_backtest

    case = make_backtest_case("history", variant="signal_pool")
    case["csvs"]["corporate_actions"] += f"VOL_01,2024-01-31,cash_dividend,1,{amount},synthetic,{published_at}\n"
    store, ref = build_case_store(tmp_path, case)
    coverage = case["actions_coverage"]
    annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                              codes=list(case["codes"]), query_basis="ex_date")
    definition = case_definition(case, ref.snapshot_id)
    signals = build_signals(definition, store)
    assert signals.loc[signals["signal_at"].eq("2023-12-31"), "code"].tolist() == ["VOL_01"]
    assert signals.loc[signals["signal_at"].eq("2024-01-31"), "code"].tolist() == expected
    bundle = tmp_path / "prepared"
    report = prepare_backtest(definition, store, bundle)
    exclusions = pd.read_parquet(bundle / "exclusions.parquet")
    action_gaps = exclusions.loc[exclusions["field"].eq("corporate_action_terms")]
    assert bool(expected) == action_gaps.empty
    if not expected:
        assert not report.can_run
        assert action_gaps[["date", "code"]].values.tolist() == [["2024-01-31", "VOL_01"]]
        assert "cash_dividend" in action_gaps["reason"].item() and "d" in action_gaps["reason"].item()
        assert signals.attrs["signal_exclusions"] == action_gaps.to_dict("records")


@pytest.mark.parametrize("kind,ex_date,available_at,record_date,missing_field", [
    ("cash_dividend", "", "2024-01-01T14:00:00+08:00", "", "ex_date"),
    ("cash_dividend", "待定", "2024-01-01T14:00:00+08:00", "", "ex_date"),
    ("rights_issue", "2024-01-31", "2024-01-31T18:00:00+08:00", "", None),
    ("rights_issue", "2024-01-31", "2024-01-31T14:55:00+08:00", "", "action_type"),
    ("cash_dividend", "待定", "2024-01-01T14:00:00+08:00", "2024-01-31", None),
], ids=["undated-dividend", "pending-ex-date", "late-rights-issue", "known-rights-issue", "proved-outside-window"])
def test_signal_action_occurrence_and_date_are_proved_before_use(
    tmp_path: Path, kind, ex_date, available_at, record_date, missing_field,
) -> None:
    from hetu_quant.backtest_data import prepare_backtest

    def run(root, action):
        case = make_backtest_case("history", variant="signal_pool")
        case["csvs"]["corporate_actions"] = (
            "code,ex_date,action_type,m,d,terms_raw,available_at,record_date\n" + action
        )
        store, ref = build_case_store(root, case)
        coverage = case["actions_coverage"]
        annotate_actions_coverage(store, ref, coverage["start"], coverage["end"],
                                  codes=list(case["codes"]), query_basis="ex_date")
        definition = case_definition(case, ref.snapshot_id)
        bundle = root / "prepared"
        report = prepare_backtest(definition, store, bundle)
        signals = build_signals(definition, store)
        assert signals.loc[signals["signal_at"].eq("2023-12-31"), "code"].tolist() == ["VOL_01"]
        exclusions = pd.read_parquet(bundle / "exclusions.parquet")
        bars = store.read_table(ref, "daily_bars")
        raw_close = bars.loc[bars["date"].eq("2024-01-31") & bars["code"].eq("VOL_01"), "close_raw"].item()
        return report, signals, exclusions.loc[exclusions["field"].eq("corporate_action_terms")], raw_close

    baseline_report, baseline, _, baseline_close = run(tmp_path / "baseline", "")
    assert baseline_report.can_run
    report, signals, gaps, raw_close = run(
        tmp_path / "action", f"VOL_01,{ex_date},{kind},1,50,synthetic,{available_at},{record_date}\n",
    )
    assert raw_close == baseline_close
    january = signals.loc[signals["signal_at"].eq("2024-01-31")]
    if missing_field:
        assert not report.can_run
        assert january.empty
        assert gaps[["date", "code"]].values.tolist() == [["2024-01-31", "VOL_01"]]
        assert missing_field in gaps["reason"].item() and "2021-01-31..2024-01-31" in gaps["reason"].item()
        assert gaps["source_ref"].item()
        assert signals.attrs["signal_exclusions"] == gaps.to_dict("records")
    else:
        assert report.can_run and gaps.empty
        expected = baseline.loc[baseline["signal_at"].eq("2024-01-31")]
        pd.testing.assert_frame_equal(january.drop(columns="source_ref"), expected.drop(columns="source_ref"))


def test_close_version_reaches_weights() -> None:
    from hetu_quant.strategies import _known_market_cap

    caps = pd.DataFrame([
        {"code": "X", "date": "2024-01-31", "market_cap_cny": 100,
         "available_at": "2024-01-31T15:00:00+08:00"},
        {"code": "X", "date": "2024-01-31", "market_cap_cny": 900,
         "available_at": "2024-01-31T18:00:00+08:00"},
    ])
    assert _known_market_cap(caps, "X", "2024-01-31") == 100
    assert _known_market_cap(caps.iloc[::-1], "X", "2024-01-31") == 100
    assert _known_market_cap(caps.iloc[1:], "X", "2024-01-31") is None


def test_after_close_target_with_or_without_next_calendar():
    from hetu_quant.backtest_data import split_signal_calendar

    days = pd.date_range("2024-01-01", "2024-02-02")
    calendar = pd.DataFrame(dict(date=days.strftime("%Y-%m-%d"), is_trading_day=days.weekday < 5))
    assert split_signal_calendar(calendar, "2024-01-31") == ("month_end", "2024-02-01")
    assert split_signal_calendar(calendar[calendar.date.le("2024-01-31")], "2024-01-31") == ("month_end", None)
    assert split_signal_calendar(calendar, "2024-01-30") == ("ordinary", "2024-01-31")


def test_after_close_target_refuses_unproved_month_end():
    from hetu_quant.backtest_data import split_signal_calendar

    days = pd.date_range("2024-04-01", "2024-05-02").difference(pd.DatetimeIndex(["2024-04-30"]))
    calendar = pd.DataFrame(dict(date=days.strftime("%Y-%m-%d"), is_trading_day=days.weekday < 5))
    assert split_signal_calendar(calendar, "2024-04-29") == ("unknown", None)


def test_after_close_signal_inputs_are_bounded_and_licensed(tmp_path):
    from after_close_cases import JAN, make_after_close_source
    from hetu_quant.backtest_data import read_low_volatility_inputs
    from hetu_quant.strategies import build_signal_targets

    store, ref = make_after_close_source(tmp_path / "valid")
    inputs = read_low_volatility_inputs(store, ref, signal_date=JAN)
    result = build_signal_targets(inputs, JAN)
    assert inputs.pool_size == 20
    assert result.status == "ready"
    assert result.targets.code.tolist() == ["VOL_01", "VOL_02"]
    assert result.targets.target_weight.tolist() == [0.25, 0.75]
    for variant, field in ((dict(no_pool=True), "historical_pool"), (dict(denied="daily_bars"), "daily_bars"),
                           (dict(denied="corporate_actions"), "corporate_actions"),
                           (dict(missing_cap=True), "market_cap_cny")):
        other, source = make_after_close_source(tmp_path / field, **variant)
        limited = build_signal_targets(read_low_volatility_inputs(other, source, signal_date=JAN), JAN)
        assert limited.status == "blocked" and limited.targets.empty
        assert field in {gap["field"] for gap in limited.gaps}
        if field == "market_cap_cny":
            assert limited.candidates.code.iloc[:2].tolist() == ["VOL_01", "VOL_02"]
            assert any(gap["code"] == "VOL_02" for gap in limited.gaps)


def test_after_close_uses_action_publication_clock(tmp_path):
    from after_close_cases import JAN, make_after_close_source
    from hetu_quant.backtest_data import read_low_volatility_inputs
    from hetu_quant.strategies import build_signal_targets

    base = dict(code="VOL_01", ex_date=str(JAN))
    for variant, action in (
        ("in_time", dict(**base, action_type="cash_dividend", d=50, available_at="2024-01-31T14:55:00+08:00")),
        ("late", dict(**base, action_type="rights_issue", available_at="2024-01-31T18:00:00+08:00")),
        ("missing", dict(**base, action_type="cash_dividend", available_at="2024-01-31T14:55:00+08:00")),
    ):
        store, ref = make_after_close_source(tmp_path / variant, action=action)
        target = build_signal_targets(read_low_volatility_inputs(store, ref, signal_date=JAN), JAN)
        if variant == "missing":
            assert target.status == "blocked" and target.targets.empty
            assert any(gap.get("missing_fields") == ["d"] for gap in target.gaps)
        else:
            assert target.status == "ready"
            expected = ["VOL_02", "VOL_03"] if variant == "in_time" else ["VOL_01", "VOL_02"]
            assert target.targets.code.tolist() == expected
