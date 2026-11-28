import pytest

from factor_cases import run_formula_case
from factor_stage02_expected import EXPECTED


@pytest.mark.parametrize(
    "factor_id",
    [
        "earnings_yield",
        "book_to_price",
        "operating_cashflow_yield",
        "free_cashflow_yield",
        "roe",
        "roic",
        "accruals",
        "gross_margin_stability",
        "leverage",
        "revenue_growth_yoy",
        "profit_growth_yoy",
        "rd_intensity",
        "expected_growth",
    ],
)
def test_statement_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_earnings_yield_local_available_version(tmp_path):
    from dataclasses import replace
    from datetime import date

    from factor_cases import example_query, local_fact_source
    from hetu_quant.factors import close_cutoff, compute_factor

    _, document, query = example_query("earnings_yield")
    tables = document["tables"]
    for row in tables["financial_reports"]:
        row["coordinate"] = "2023Q4"
    store, ref = local_fact_source(tmp_path / "dual", tables)
    result = compute_factor(store, ref, query)
    assert result.rows[0]["value"] == pytest.approx(0.06)
    assert result.rows[0]["components"]["numerator"] == 600_000_000
    assert result.rows[0]["inputs"][0]["evidence_ref"] == "factor-fields.json"
    following = date(2024, 2, 1)
    result = compute_factor(store, ref, replace(query, trade_date=following, cutoff=close_cutoff(following)))
    assert result.rows[0]["value"] == pytest.approx(0.08)
    assert result.rows[0]["inputs"][0]["available_at"] == "2024-01-31T18:00:00+08:00"
    tables["financial_reports"].pop(0)
    store, ref = local_fact_source(tmp_path / "late-only", tables)
    result = compute_factor(store, ref, query)
    assert result.status == "unavailable"
    assert not result.rows
    assert result.gaps[0]["field"] == "net_profit_ex"


@pytest.mark.parametrize(("field_unit", "older_report", "mismatch"),
                         [(False, False, None), (True, False, None), (False, True, None),
                          (False, True, "amount"), (False, True, "amount-and-clock"), (False, True, "missing"),
                          (False, False, "revoked-field")],
                         ids=["report-unit", "field-unit", "two-quarters", "latest-mismatch", "mismatched-clock",
                              "missing-latest-row", "revoked-field"])
def test_earnings_yield_from_native_financial_and_market_cap_tables(
    monkeypatch, tmp_path, field_unit, older_report, mismatch,
):
    import hashlib
    import json

    import pandas as pd

    from backtest_cases import build_case_store
    from factor_cases import example_query
    from hetu_quant.factors import compute_factor

    _, _, query = example_query("earnings_yield")
    time = "2024-01-31T14:55:00+08:00"
    finance = dict(
        code="A",
        report_period="2023-12-31",
        ann_date="2024-01-31",
        available_at=time,
        net_profit_ex=60_000 if field_unit else 6,
        currency="CNY",
        unit="亿元",
    )
    cap = dict(code="A", date="2024-01-31", market_cap_cny=10_000_000_000, available_at=time)
    originals, receipts = [], []
    registered = [
        ("financial_reports", finance, "2023Q4", ("net_profit_ex", "currency", "unit")),
        ("market_cap", cap, "2024-01-31", ("market_cap_cny",)),
    ]
    past = dict(finance, report_period="2023-09-30", net_profit_ex=4)
    if older_report:
        registered.insert(0, ("financial_reports", past, "2023Q3", ("net_profit_ex", "currency", "unit")))
    for table, values, period, fields in registered:
        for field in fields:
            key = f"A|{period}|{field}"
            originals.append(
                dict(
                    product_id=f"factor_{table}",
                    record_key=key,
                    revision_id="1",
                    published_at=time,
                    value=values[field],
                )
            )
            if field == "net_profit_ex":
                originals[-1]["unit"] = "万元" if field_unit else "亿元"
                originals[-1]["currency"] = "CNY"
                if period == "2023Q4" and mismatch == "missing":
                    originals[-1].update(announcement_ref="issuer-A-Q4.pdf", coordinate="issuer-A-Q4-line17")
            receipts.append(
                dict(
                    table_name=table,
                    record_key=key,
                    revision_id="1",
                    published_at=time,
                    verified=True,
                    evidence_ref="native-factor-facts.json",
                )
            )
    payload = json.dumps(dict(records=originals), ensure_ascii=False).encode()
    for proof in receipts:
        proof["sha256"] = hashlib.sha256(payload).hexdigest()
    latest = dict(finance, net_profit_ex=8) if mismatch in ("amount", "amount-and-clock") else finance
    if mismatch == "amount-and-clock":
        latest["available_at"] = "2024-01-31T14:56:00+08:00"
    reports = [past] if older_report else []
    if mismatch != "missing":
        reports.append(latest)
    tables = {"financial_reports": reports,
              "market_cap": [cap], "knowledge_evidence": receipts}
    case = dict(
        csvs={table: pd.DataFrame(rows).to_csv(index=False) for table, rows in tables.items()},
        table_products={table: (f"factor_{table}", "catalog") for table in tables},
        root_config=True,
        obtained_at="2024-02-01T18:00:00+08:00",
        permissions={table: {"allowed_uses": ["factor_calculation"]} for table in tables},
    )
    store, ref = build_case_store(tmp_path, case)
    (store.root / "evidence" / "native-factor-facts.json").write_bytes(payload)
    result = compute_factor(store, ref, query)
    if mismatch == "revoked-field":
        assert result.rows[0]["value"] == pytest.approx(.06)
        policy = store.config["products"]["factor_financial_reports"]["permission"]
        policy["allowed_fields"] = ["code", "report_period", "ann_date", "available_at", "unit", "currency"]
        columns_read, proof_reads = [], []
        original_range = store.read_range
        original_register = store.read_evidence_registrations

        def reader(*args, **kwargs):
            if args[1] == "financial_reports":
                columns_read.extend((*kwargs["columns"], *kwargs.get("evidence_columns", ())))
            return original_range(*args, **kwargs)

        def registrations(*args, **kwargs):
            frame = original_register(*args, **kwargs)
            if kwargs["table"] == "financial_reports":
                proof_reads.extend(frame.record_key.tolist())
            return frame

        monkeypatch.setattr(store, "read_range", reader)
        monkeypatch.setattr(store, "read_evidence_registrations", registrations)
        denied = compute_factor(store, ref, query)
        assert "net_profit_ex" not in columns_read
        assert not any(key.endswith("|net_profit_ex") for key in proof_reads)
        assert not denied.rows and any(gap["field"] == "net_profit_ex" for gap in denied.gaps)
        print("native E/P 6% before; after denial:", columns_read, proof_reads, denied.gaps)
        return
    if mismatch:
        assert not result.rows
        gap, = result.gaps
        assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")
        evidence, = gap["evidence"]
        assert (evidence["value"], evidence["unit"]) == (6, "亿元")
        assert evidence["evidence_ref"] == "native-factor-facts.json"
        assert evidence["available_at"] == time
        if mismatch == "missing":
            from test_factor_workbench import open_factors, run_trial

            assert "缺少" in gap["reason"]
            assert evidence["observation_status"] == "unavailable_at_cutoff"
            assert evidence["fact_key"] == "A|2023Q4|net_profit_ex"
            assert "observed_value" not in evidence
            page = open_factors(monkeypatch, tmp_path)
            monkeypatch.setenv("HETU_DATA_ROOT", str(store.root))
            page.selectbox(key="factor_id").set_value("earnings_yield").run()
            page.selectbox(key="factor_source_earnings_yield").set_value(ref.snapshot_id).run()
            run_trial(page)
            assert not page.exception
            expander = next(item for item in page.expander if item.label == "缺口与冲突原件")
            assert json.loads(expander.json[0].value) == [gap]
            print("native Q4 unavailable in compute_factor and workbench", expander.json[0].value)
            page.button(key="factor_evidence_gap_0").click().run()
            assert page.sidebar.radio(key="page_nav").value == "数据"
            assert any("2024-01-31 15:00:00+08:00" in caption.value for caption in page.caption)
            assert any("issuer-A-Q4.pdf" in payload.value and "2023Q4" in payload.value for payload in page.json)
            page.button(key="factor_return_to_draft").click().run()
            assert page.selectbox(key="factor_source_earnings_yield").value == ref.snapshot_id

            from test_factor_recheck import library

            def with_latest(value, *, attachment="native-factor-facts.json", original_ref=None,
                            profit_unit="亿元", registered_value=6, registration=True,
                            coordinate="issuer-A-Q4-line17"):
                registered = json.loads(payload)
                for record in registered["records"]:
                    if record["record_key"] == "A|2023Q4|net_profit_ex":
                        record.update(value=registered_value, unit=profit_unit)
                        if original_ref:
                            record["announcement_ref"] = original_ref
                        if not registration:
                            record.pop("announcement_ref")
                        if not registration or coordinate is None:
                            record.pop("coordinate")
                        else:
                            record["coordinate"] = coordinate
                    if record["record_key"] == "A|2023Q4|unit":
                        record["value"] = profit_unit
                attached = json.dumps(registered, ensure_ascii=False).encode()
                reports = [past] if value is None else [past, dict(latest, net_profit_ex=value, unit=profit_unit)]
                csvs = dict(case["csvs"], financial_reports=pd.DataFrame(reports).to_csv(index=False),
                            knowledge_evidence=pd.DataFrame(receipts).assign(
                    evidence_ref=attachment, sha256=hashlib.sha256(attached).hexdigest(),
                ).to_csv(index=False))
                owner, snapshot = build_case_store(tmp_path / f"{value}-{attachment}", dict(case, csvs=csvs))
                (owner.root / "evidence" / attachment).write_bytes(attached)
                return owner, snapshot

            renamed = "issuer-attestations-copy.json"
            for amount, unit in ((6, "亿元"), (60_000, "万元")):
                matched = with_latest(amount, attachment=renamed, profit_unit=unit, registered_value=amount)
                single = compute_factor(matched[0], matched[1], query)
                assert single.rows[0]["value"] == pytest.approx(.06)
                assert single.rows[0]["inputs"][0]["field_evidence"]["net_profit_ex"]["evidence_ref"] == renamed
                for assets in (((store, ref), matched), (matched, (store, ref))):
                    combined = compute_factor(store, library(store, *assets), query)
                    assert not combined.gaps and combined.rows[0]["value"] == pytest.approx(.06)
                    quote = combined.rows[0]["inputs"][0]["field_evidence"]["net_profit_ex"]
                    assert (quote["value"], quote["unit"], quote["evidence_ref"]) == (amount, unit, renamed)
                    corroborating, = quote["corroborating_sources"]
                    assert (corroborating["value"], corroborating["unit"], corroborating["evidence_ref"]) == (
                        6, "亿元", "native-factor-facts.json",
                    )
                    assert quote["announcement_ref"] == corroborating["announcement_ref"] == "issuer-A-Q4.pdf"
                    print("same Q4 registration, both asset locations", json.dumps(quote, ensure_ascii=False))
            different = with_latest(80_000, attachment=renamed, profit_unit="万元", registered_value=80_000)
            for assets in (((store, ref), different), (different, (store, ref))):
                combined = compute_factor(store, library(store, *assets), query)
                assert not combined.rows
                gap, = combined.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")
                assert {(item["value"], item["unit"], item["evidence_ref"]) for item in gap["evidence"]} == {
                    (6, "亿元", "native-factor-facts.json"), (80_000, "万元", renamed),
                }
                print("conflicting original Q4 amounts", json.dumps(gap, ensure_ascii=False))
            combined = compute_factor(store, library(store, (store, ref), with_latest(8)), query)
            assert not combined.rows
            assert any(float(item["observed_value"]) == 8 and item["value"] == 6
                       for item in combined.gaps[0]["evidence"] if "observed_value" in item)
            separate = with_latest(6, original_ref="independent-Q4.pdf")
            combined = compute_factor(store, library(store, (store, ref), separate), query)
            assert not combined.rows and combined.gaps[0]["evidence"][0]["announcement_ref"] == "issuer-A-Q4.pdf"
            print("different Q4 announcement", json.dumps(combined.gaps[0], ensure_ascii=False))
            unlinked_missing = with_latest(None, registration=False)
            unlinked_observed = with_latest(60_000, registration=False, profit_unit="万元", registered_value=60_000)
            for assets in ((unlinked_missing, unlinked_observed), (unlinked_observed, unlinked_missing)):
                unlinked = compute_factor(store, library(store, *assets), query)
                assert not unlinked.rows
                gap, = unlinked.gaps
                assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")
                missing, = gap["evidence"]
                assert missing["snapshot_id"] == unlinked_missing[1].snapshot_id
                assert (missing["value"], missing["unit"], missing["available_at"]) == (6, "亿元", time)
                assert not missing.get("announcement_ref") and not missing.get("coordinate")
                print("unlinked Q4 registration", json.dumps(gap, ensure_ascii=False))

            notice = "https://issuer.example/A/2023Q4/announcement-517"
            notice_left = with_latest(None, attachment="left.json", original_ref=notice)
            notice_right = with_latest(
                60_000, attachment="right.json", original_ref=notice, profit_unit="万元",
                registered_value=60_000, coordinate=None,
            )
            for assets in ((notice_left, notice_right), (notice_right, notice_left)):
                combined = compute_factor(store, library(store, *assets), query)
                assert not combined.gaps
                assert combined.rows[0]["value"] == pytest.approx(.06)
                quote = combined.rows[0]["inputs"][0]["field_evidence"]["net_profit_ex"]
                corroborating, = quote["corroborating_sources"]
                assert (quote["value"], quote["unit"], quote["evidence_ref"]) == (60_000, "万元", "right.json")
                assert (corroborating["value"], corroborating["unit"], corroborating["evidence_ref"]) == (
                    6, "亿元", "left.json",
                )
                assert quote["announcement_ref"] == corroborating["announcement_ref"] == notice
                assert "coordinate" not in quote and corroborating["coordinate"] == "issuer-A-Q4-line17"
                print("Q4 with one coordinate and a shared publication", json.dumps(quote, ensure_ascii=False))

            incompatible = with_latest(
                60_000, attachment="other-coordinate.json", original_ref=notice, profit_unit="万元",
                registered_value=60_000, coordinate="different-receipt-row",
            )
            combined = compute_factor(store, library(store, notice_left, incompatible), query)
            assert not combined.rows
            gap, = combined.gaps
            assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")

            disagreement = with_latest(
                80_000, attachment="conflicting-amount.json", original_ref=notice, profit_unit="万元",
                registered_value=80_000, coordinate=None,
            )
            combined = compute_factor(store, library(store, notice_left, disagreement), query)
            assert not combined.rows
            gap, = combined.gaps
            assert (gap["subject"], gap["period"], gap["field"]) == ("A", "2023Q4", "net_profit_ex")
            assert {(item["value"], item["unit"], item["evidence_ref"]) for item in gap["evidence"]} == {
                (6, "亿元", "left.json"), (80_000, "万元", "conflicting-amount.json"),
            }
            print("Q4 with one coordinate and conflicting amounts", json.dumps(gap, ensure_ascii=False))
            return
        assert float(evidence["observed_value"]) == 8
        assert evidence["observed_source_ref"].endswith("/financial_reports.csv")
        assert evidence["observed_at"] == latest["available_at"]
        print("native Q4 mismatched", json.dumps(gap, ensure_ascii=False))
        return
    assert result.rows and result.rows[0]["value"] == pytest.approx(0.06)
    assert result.rows[0]["inputs"][0]["period"] == "2023Q4"
    assert all(row["evidence_ref"] == "native-factor-facts.json" for row in result.rows[0]["inputs"])
    if older_report:
        print("native Q4 matched", json.dumps(result.rows[0]["components"], ensure_ascii=False))
    if field_unit:
        proof = result.rows[0]["inputs"][0]["field_evidence"]["net_profit_ex"]
        assert (proof["value"], proof["unit"]) == (60_000, "万元")
