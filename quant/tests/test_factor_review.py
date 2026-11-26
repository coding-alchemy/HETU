"""Regression cases built from the approved teaching facts."""

import json
from dataclasses import replace

import pytest
from factor_assertions import checked
from factor_cases import example_query, local_fact_source
from hetu_quant.factors import TeachingSource, compute_factor


def rerun(tmp_path, source, doc, query):
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    print(query.factor_id, [(row["value"], row["unit"]) for row in result.rows], result.gaps)
    return result


@pytest.mark.parametrize("local", [False, True])
def test_report_conflict_is_not_older_earnings(tmp_path, local):
    source, doc, request = example_query("earnings_yield")
    latest = doc["tables"]["financial_reports"][0]
    latest.update(fact_key="2023Q4", coordinate="2023Q4", revision_id="a", source_ref="原件甲")
    other = dict(latest, revision_id="b", net_profit_ex=7, source_ref="原件乙")
    older = dict(latest, fact_key="2023Q3", coordinate="2023Q3", period="2023Q3", net_profit_ex=4)
    doc["tables"]["financial_reports"] = [older, latest, other]

    def run(name):
        if local:
            store, ref = local_fact_source(tmp_path / name, doc["tables"])
            result = compute_factor(store, ref, request)
            print(name, result.rows, result.gaps)
            return result
        return rerun(tmp_path, source, doc, request)

    result = run("conflict")
    assert not result.rows
    gap = result.gaps[0]
    assert (gap["subject"], gap["field"], gap["period"]) == ("A", "net_profit_ex", "2023Q4")
    assert {item["source_ref"] for item in gap["evidence"]} == {"原件甲", "原件乙"}
    other["supersedes"] = "a"
    doc["tables"]["financial_reports"].append(dict(older, revision_id="c", net_profit_ex=3))
    result = run("resolved")
    assert result.rows[0]["value"] == pytest.approx(0.07)
    assert result.gaps == []


def test_historical_amount_uses_each_close_cutoff(tmp_path):
    source, doc, query = example_query("market_amount_percentile")
    rows = doc["tables"]["daily_bars"]
    row = rows[0]
    row.update(revision_id="original", source_ref="昨日成交")
    rows.append(
        dict(
            row,
            amount_cny=300,
            revision_id="new",
            supersedes="original",
            available_at="2024-01-31T14:00:00+08:00",
            source_ref="今日修订",
        )
    )
    result = rerun(tmp_path, source, doc, query).rows[0]
    assert result["value"] == 100
    assert result["components"]["history"] == [80, 100]
    assert result["components"]["coverage"][0]["denominator"] == 2
    assert any(row.get("source_ref") == "昨日成交" for row in result["inputs"])


def test_historical_sector_turnover_keeps_prior_float(tmp_path):
    source, doc, query = example_query("sector_turnover_percentile")
    row = doc["tables"]["shares"][0]
    row["revision_id"] = "old"
    doc["tables"]["shares"].append(
        dict(row, revision_id="new", supersedes="old", float_shares=100, available_at="2024-01-31T14:00:00+08:00")
    )
    result = rerun(tmp_path, source, doc, query).rows[0]
    assert result["components"]["dates"] == ["2024-01-30", "2024-01-31"]
    assert result["components"]["values"] == pytest.approx([0.10, 0.09])
    assert result["value"] == 0
    assert [(part["base"], part["denominator"], part["numerator"]) for part in result["components"]["coverage"]] == [
        (20, 1, 2),
        (20, 1, 1.8),
    ]


def test_same_day_split_and_cash_use_one_old_share(tmp_path):
    source, doc, query = example_query("low_volatility_36m")
    bars = doc["tables"]["daily_bars"]
    for row in bars:
        row["close_raw"] = 10 if row is bars[0] else 4.5
    date = bars[1]["date"]
    events = doc["tables"]["corporate_actions"]
    stamp = "2024-01-30T09:00:00+08:00"
    events.extend(
        [
            dict(code="A", ex_date=date, m=2, d=0, action_type="split", available_at=stamp),
            dict(code="A", ex_date=date, m=1, d=1, action_type="cash_dividend", available_at=stamp),
        ]
    )
    result = rerun(tmp_path, source, doc, query).rows[0]
    assert result["components"]["returns"] == pytest.approx([0] * 36)
    assert result["value"] == pytest.approx(0)
    events[0].pop("d")
    events[1].pop("m")
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(0)
    required_cash = events[1].pop("d")
    gap = rerun(tmp_path, source, doc, query).gaps[0]
    assert gap["field"] == "d" and gap["period"] == date
    events[1]["d"] = required_cash
    events.append(dict(events[-1], action_type="rights_issue", ex_date="2024-02-02"))
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(0)
    events[-1]["ex_date"] = date
    result = rerun(tmp_path, source, doc, query)
    assert not result.rows and result.gaps[0]["field"] == "action_type"


def test_continuous_window_ignores_earlier_bars(tmp_path):
    from datetime import date

    from hetu_quant.factors import close_cutoff

    source, doc, query = example_query("sma")
    day = date(2024, 4, 1)
    query = replace(query, trade_date=day, cutoff=close_cutoff(day))
    dates = ("2024-03-28", "2024-03-29", "2024-04-01")
    bars = doc["tables"]["daily_bars"]
    for row, calendar, timestamp in zip(bars, doc["tables"]["calendar"], dates, strict=True):
        row.pop("close")
        row.update(date=timestamp, close_raw=100, available_at=timestamp + "T15:00:00+08:00")
        calendar["date"] = timestamp
    doc["tables"]["action_coverage"] = [
        dict(code="A", start=dates[0], end=dates[-1], available_at=bars[-1]["available_at"])
    ]
    bars.insert(0, dict(bars[0], date="2024-01-02", close_raw=300, available_at="2024-01-02T15:00:00+08:00"))
    result = rerun(tmp_path, source, doc, query)
    assert result.rows[0]["value"] == 100
    assert result.rows[0]["unit"] == "元/股"
    assert tuple(row["date"] for row in result.rows[0]["inputs"]) == dates


def test_up_amount_requires_comparable_window(tmp_path):
    source, doc, query = example_query("up_amount_share")
    doc["tables"].pop("action_coverage")
    result = rerun(tmp_path, source, doc, query)
    assert not result.rows and result.gaps[0]["field"] == "close"
    bars = doc["tables"]["daily_bars"]
    for row in bars:
        row["close"] = row["close_raw"]
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == 0.4


@pytest.mark.parametrize("unknown", ["待定", None])
def test_dividend_unknown_date_scoped_to_window(tmp_path, unknown):
    source, doc, query = example_query("dividend_yield")
    events = doc["tables"]["corporate_actions"]
    events.append(dict(events[0], ex_date=unknown, event_id="pending", source_ref="待核公告"))
    result = rerun(tmp_path, source, doc, query)
    assert not result.rows and result.gaps[0]["field"] == "ex_date"
    assert result.gaps[0]["event"] == "pending"
    events[-1]["latest_ex_date"] = "2022-01-01"
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(0.05)


def test_actual_holding_uses_disclosure_window(tmp_path):
    source, doc, query = example_query("insider_net_purchase")
    events = doc["tables"]["events"]
    events[0].update(effective_date="2023-12-20", available_at="2024-01-15T10:00:00+08:00")
    events[1]["available_at"] = "2024-01-16T10:00:00+08:00"
    result = rerun(tmp_path, source, doc, query).rows[0]
    assert result["value"] == pytest.approx(0.02)
    assert result["inputs"][0]["effective_date"] == "2023-12-20"
    events[0]["kind"] = "sale_plan"
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(-0.01)


@pytest.mark.parametrize(
    ("currency", "market", "field"),
    [
        ("USD", "SH", "currency"),
        ("CNY", "US", "calendar"),
        ("USD", "US", "currency"),
        ("CNY", "SH", None),
    ],
)
def test_return_correlation_requires_comparability(tmp_path, request, factor_evidence_root, currency, market, field):
    source, doc, query = example_query("rolling_return_correlation")
    for row in doc["tables"]["daily_bars"]:
        row.update(currency="CNY", market="SH", return_basis="price")
        if row["code"] == "B":
            row.update(currency=currency, market=market)
    expected = {"field": field, "rows": 0} if field else {"value": 1, "unit": "相关系数", "observations": 3}
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        query.factor_id,
        f"formula:alignment-{currency}-{market}",
        expected,
        replay=(query, doc),
        inputs=dict(
            case=source.case_id,
            second_leg=dict(currency=currency, market=market),
            day=str(query.trade_date),
            subjects=query.subjects,
        ),
    ) as actual:
        result = rerun(tmp_path, source, doc, query)
        actual.update(status=result.status, rows=result.rows, gaps=result.gaps)
        if field:
            assert not result.rows and result.gaps[0]["field"] == field
            assert result.gaps[0]["subject"] == ["A", "B"]
        else:
            assert result.rows[0]["value"] == pytest.approx(1)
            assert result.rows[0]["unit"] == "相关系数"
            assert result.rows[0]["components"]["observations"] == 3


def test_premium_needs_same_currency(tmp_path):
    source, doc, query = example_query("etf_premium")
    doc["tables"]["daily_bars"][0]["currency"] = "USD"
    result = rerun(tmp_path, source, doc, query)
    assert not result.rows and result.gaps[0]["field"] == "currency"


def test_expected_growth_selects_applicable_adjacent_years(tmp_path):
    source, doc, query = example_query("expected_growth")
    forecasts = doc["tables"]["forecasts"]
    forecasts.append(dict(forecasts[-1], period="2026"))
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(0.3)
    del forecasts[-1]
    for row, year in zip(forecasts, ("2020", "2021"), strict=True):
        row["period"] = year
    result = rerun(tmp_path, source, doc, query)
    assert not result.rows and result.gaps[0]["field"] == "forecasts"


def test_surprise_uses_declared_consensus(tmp_path):
    source, doc, query = example_query("earnings_surprise", params={"forecast_aggregation": "equal_weight"})
    forecasts = doc["tables"]["forecasts"]
    forecasts[0]["institution"] = "X"
    forecasts[0].pop("forecast_kind", None)
    forecasts.append(
        dict(
            forecasts[0],
            fact_key="second",
            institution="Y",
            forecast_net_profit=14,
            available_at="2024-01-02T14:00:00+08:00",
        )
    )
    row = rerun(tmp_path, source, doc, query).rows[0]
    assert row["value"] == 0
    assert row["components"]["institutions"] == ["X", "Y"]
    assert row["components"]["expected"] == 1.2e9
    forecasts.append(
        dict(forecasts[0], fact_key="consensus", institution=None, forecast_kind="consensus", forecast_net_profit=10)
    )
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(0.2)


def test_pair_beta_requires_only_training_prices(tmp_path):
    source, doc, query = example_query("pair_hedge_beta")
    doc["tables"]["daily_bars"] = [row for row in doc["tables"]["daily_bars"] if row["date"] != str(query.trade_date)]
    assert rerun(tmp_path, source, doc, query).rows[0]["value"] == pytest.approx(2)
    assert not rerun(tmp_path, source, doc, replace(query, factor_id="pair_spread")).rows


def test_limit_count_and_failed_limit_have_different_dependencies(tmp_path, request, factor_evidence_root):
    source, doc, query = example_query("limit_up_count")
    row = next(
        row for row in doc["tables"]["daily_bars"] if row["code"] == "A" and row["date"] == str(query.trade_date)
    )
    row.pop("high_raw")
    for limit in doc["tables"]["price_limits"]:
        limit.pop("lower")
    inputs = dict(case=source.case_id, day=str(query.trade_date), missing=["A.high_raw", "price_limits.lower"])
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        "limit_up_count",
        "formula:required_fields",
        {"value": 1, "unit": "家", "sealed": ["A"]},
        inputs=inputs,
        replay=(query, doc),
    ) as actual:
        result = rerun(tmp_path, source, doc, query)
        sealed = result.rows[0]
        actual.update(value=sealed["value"], unit=sealed["unit"], sealed=sealed["components"]["matches"])
        assert actual == {"value": 1, "unit": "家", "sealed": ["A"]}
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        "failed_limit_up_share",
        "formula:required_fields",
        {"status": "partial", "subject": "A", "field": "high_raw", "full_scope": False},
        inputs=inputs,
        replay=(replace(query, factor_id="failed_limit_up_share"), doc),
    ) as actual:
        failed = rerun(tmp_path, source, doc, replace(query, factor_id="failed_limit_up_share"))
        actual.update(status=failed.status, gaps=failed.gaps, components=failed.rows[0]["components"])
        assert failed.status == "partial"
        assert failed.rows[0]["components"]["full_scope"] is False
        assert any(gap["subject"] == "A" and gap["field"] == "high_raw" for gap in failed.gaps)


def test_weighted_score_resolves_child_factor_parameters(tmp_path):
    source, doc, query = example_query(
        "weighted_factor_score",
        params={
            "components": {"traded_amount": {"weight": 1, "direction": 1}},
        },
    )
    for index, row in enumerate(doc["tables"]["daily_bars"]):
        row["amount_cny"] = 1 + index // 2
    rows = rerun(tmp_path, source, doc, query).rows
    assert [row["value"] for row in rows] == pytest.approx([-1 / 1.4836, 0, 1 / 1.4836])
    assert rows[0]["components"]["components"]["traded_amount"]["parameters"]["mode"] == "daily"
    query.parameters["components"]["traded_amount"]["parameters"] = {"mode": []}
    result = rerun(tmp_path, source, doc, query)
    assert any(gap["field"] == "components.traded_amount.parameters.mode" for gap in result.gaps)


def test_one_day_native_paid_amount_uses_narrow_grant(tmp_path, monkeypatch):
    from datetime import date

    from factor_cases import local_volume_source
    from hetu_quant.data import AccessDenied, DataStore

    path = tmp_path / "volume"
    original, _ = local_volume_source(path, code="A.SH")
    grant_path = original.root / "data-entitlements.json"
    scope = dict(
        product_id="factor_daily",
        codes=["A.SH"],
        markets=["SH"],
        start="2024-01-08",
        end="2024-04-01",
        approved_scope_ref="合成用例",
        license_ref="合成许可",
        allowed_operations=["read", "import"],
        acquire_until="2099-12-31",
        retained_use=True,
        allowed_uses=["factor_calculation"],
    )
    grant_path.write_text(json.dumps({"grants": [scope]}))
    config = original.root / "data-products.json"
    edit = json.loads(config.read_text())
    edit["products"]["factor_daily"]["fee_class"] = "paid"
    config.write_text(json.dumps(edit))
    manifest = path / "manifest.json"
    edit = json.loads(manifest.read_text())
    edit["mode"] = "paid"
    next(item for item in edit["items"] if item["product_id"] == "factor_daily")["source"]["fee_class"] = "paid"
    manifest.write_text(json.dumps(edit))
    store = DataStore(original.root)
    ref = store.import_files(manifest, tuple(path / part for item in edit["items"] for part in item["files"]))
    grant_path.write_text(json.dumps({"grants": [dict(scope, start="2024-04-01")]}))
    _, _, request = example_query("up_amount_share")
    request = replace(request, factor_id="traded_amount", subjects=("A.SH",))
    with pytest.raises(AccessDenied, match="period grant"):
        store.read_range(
            ref,
            "daily_bars",
            codes=("A.SH",),
            start=date(1990, 1, 1),
            end=request.trade_date,
            columns=("code", "date", "product_id", "source_ref", "amount_cny", "close_raw"),
        )
    native = store.read_range
    reads = []

    def track(ref, table, **kwargs):
        if table == "daily_bars":
            reads.append(kwargs)
        return native(ref, table, **kwargs)

    monkeypatch.setattr(store, "read_range", track)
    result = compute_factor(store, ref, request)
    print("single", result.rows, result.gaps)
    row = result.rows[0]
    assert (row["value"], row["unit"], row["components"]["count"]) == (15000000, "人民币元", 1)
    assert row["inputs"][0]["date"] == "2024-04-01"
    assert row["inputs"][0]["field_evidence"]["amount_cny"]["product_id"] == "factor_daily"
    assert reads[0]["start"] == reads[0]["end"] == request.trade_date
    assert reads[0]["codes"] == ("A.SH",) and "close_raw" not in reads[0]["columns"]
    result = compute_factor(store, ref, replace(request, parameters={"mode": "mean", "window": 2}))
    print("history", result.rows, result.gaps)
    assert not result.rows and result.gaps[0]["period"] == "2024-03-29"
    assert "授权" in result.gaps[0]["reason"]


def test_native_amount_rejects_usd_original(tmp_path):
    from factor_cases import local_volume_source

    store, ref = local_volume_source(tmp_path, amount_unit="USD")
    _, _, request = example_query("up_amount_share")
    result = compute_factor(store, ref, replace(request, factor_id="traded_amount"))
    assert not result.rows
    gap = result.gaps[0]
    assert (gap["subject"], gap["field"], gap["period"]) == ("SYN_A", "currency", "2024-04-01")
    assert gap["evidence"][0]["field_evidence"]["amount_cny"]["unit"] == "USD"


def test_financial_component_selects_actual_dependencies(tmp_path):
    source, doc, query = example_query(
        "weighted_factor_score",
        params={
            "components": {"earnings_yield": {"weight": 1, "direction": 1}},
        },
    )
    _, e_p, _ = example_query("earnings_yield")
    tables = doc["tables"]
    tables.pop("daily_bars")
    tables.pop("shares")
    tables["financial_reports"] = [
        dict(e_p["tables"]["financial_reports"][0], code=code, net_profit_ex=value)
        for code, value in zip("ABC", (5, 6, 7), strict=True)
    ]
    tables["market_cap"] = [dict(e_p["tables"]["market_cap"][0], code=code) for code in "ABC"]
    result = rerun(tmp_path, source, doc, query)
    assert not result.gaps
    details = [row["components"]["components"]["earnings_yield"] for row in result.rows]
    assert [row["raw"] for row in details] == pytest.approx([0.05, 0.06, 0.07])
    assert [row["value"] for row in result.rows] == pytest.approx([-0.01 / 0.015826, 0, 0.01 / 0.015826])
    for row in result.rows:
        assert any(item.get("period") == "2023Q4" for item in row["inputs"])


@pytest.mark.parametrize("local", [False, True])
def test_dividend_conflict_is_not_a_partial_sum(tmp_path, local):
    source, doc, request = example_query("dividend_yield")
    events = doc["tables"]["corporate_actions"]
    events[0].update(coordinate="same", event_id="D1", revision_id="a", source_ref="分红甲")
    other = dict(events[0], revision_id="b", d=0.9, source_ref="分红乙")
    events.append(other)

    def run(name):
        if local:
            store, ref = local_fact_source(tmp_path / name, doc["tables"])
            return compute_factor(store, ref, request)
        return rerun(tmp_path, source, doc, request)

    result = run("conflicting")
    assert not result.rows and result.gaps[0]["event"] == "D1"
    assert result.gaps[0]["field"] == "d"
    assert {entry["source_ref"] for entry in result.gaps[0]["evidence"]} == {"分红甲", "分红乙"}
    other["supersedes"] = "a"
    assert run("resolved").rows[0]["value"] == pytest.approx(0.12)


def test_out_of_window_distribution_conflict_does_not_block_current_rate(tmp_path):
    source, doc, query = example_query("dividend_yield")
    action = dict(
        doc["tables"]["corporate_actions"][0],
        event_id="old",
        ex_date="2022-12-31",
        revision_id="old-a",
        source_ref="旧公告甲",
    )
    doc["tables"]["corporate_actions"].remove(doc["tables"]["corporate_actions"][0])
    doc["tables"]["events"] = [action, dict(action, revision_id="old-b", d=0.9, source_ref="旧公告乙")]
    result = rerun(tmp_path, source, doc, query)
    assert not result.gaps
    assert result.rows[0]["value"] == pytest.approx(0.03)
