import json
from dataclasses import replace

import pytest

from factor_cases import example_query, local_fact_source, run_formula_case
from factor_stage03_expected import EXPECTED
from hetu_quant.factors import TeachingSource, compute_factor


@pytest.mark.parametrize(
    "factor_id",
    [
        "earnings_surprise",
        "earnings_growth_change",
        "post_announcement_drift",
        "earnings_revision",
        "analyst_up_share",
        "analyst_revision_diffusion",
    ],
)
def test_disclosure_case(tmp_path, factor_evidence_root, request, factor_id):
    run_formula_case(tmp_path, factor_evidence_root, request.node.nodeid, factor_id, *EXPECTED[factor_id])


def test_surprise_uses_original_forecast_after_its_revision(tmp_path):
    source, document, query = example_query("earnings_surprise")
    forecasts = document["tables"]["forecasts"]
    forecasts[0]["revision_id"] = "original"
    forecasts.append(
        dict(
            forecasts[0],
            revision_id="new",
            supersedes="original",
            available_at="2024-01-31T13:00:00+08:00",
            forecast_net_profit=20,
        )
    )
    path = tmp_path / f"{source.case_id}.json"
    path.write_text(json.dumps(document))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    assert result.rows
    assert result.rows[0]["value"] == pytest.approx(0.2)
    assert result.rows[0]["inputs"][1]["revision_id"] == "original"


@pytest.mark.parametrize("registration", ["same-table", "duplicate", "split", "missing-first", "foreign-v1"])
def test_surprise_uses_original_disclosure_after_its_revision(tmp_path, registration):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    tables.pop("initial_reports")
    first, = tables["financial_reports"]
    first.update(coordinate="registered-announcement", announcement_ref="earnings-announcement",
                 available_at="2024-01-31T14:00:00+08:00", source_ref="first.pdf", revision_id="v1")
    correction = dict(first, revision_id="v2", supersedes="v1", source_ref="corrected.pdf",
                      available_at="2024-01-31T18:00:00+08:00", parent_net_profit=13)
    if registration in ("same-table", "duplicate"):
        tables["financial_reports"].append(correction)
    if registration == "duplicate":
        tables["initial_reports"] = [dict(correction, coordinate="correction-copy")]
    elif registration in ("split", "missing-first", "foreign-v1"):
        tables["initial_reports"] = [correction]
        if registration == "missing-first":
            tables["financial_reports"] = []
        elif registration == "foreign-v1":
            tables["financial_reports"] = [dict(first, first_disclosure=False, announcement_ref="another-notice")]
    forecast, = tables["forecasts"]
    forecast["source_ref"] = "before.pdf"
    tables["forecasts"].append(dict(forecast, fact_key="after", coordinate="after", forecast_net_profit=20,
                                   available_at="2024-01-31T16:00:00+08:00", source_ref="after.pdf"))
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    store, ref = local_fact_source(tmp_path / "local", tables)
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                       compute_factor(store, ref, request)):
            if registration in ("missing-first", "foreign-v1"):
                assert not result.rows and result.gaps[0]["field"] == "initial_reports"
                if hour == 19:
                    gap = result.gaps[0]
                    assert gap["period"] == "2023Q4"
                    assert "corrected.pdf" in json.dumps(gap["evidence"])
                    assert "缺可核对的首版" in gap["reason"]
                continue
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.20)
            assert [row["components"][key] for key in ("disclosed", "expected", "first_known_at", "released_at")] == [
                1_200_000_000, 1_000_000_000, first["available_at"], first["available_at"],
            ]
            disclosed, baseline = row["inputs"]
            assert disclosed["source_ref"] == "first.pdf" and baseline["source_ref"] == "before.pdf"
            if hour == 15:
                assert "corrected.pdf" not in json.dumps(row)
            else:
                fields = ("parent_net_profit", "available_at", "source_ref", "supersedes")
                amendments = disclosed["disclosure_revisions"]
                assert {tuple(amendment[key] for key in fields) for amendment in amendments} == {
                    (13, correction["available_at"], "corrected.pdf", "v1"),
                }
                if registration == "duplicate":
                    assert {amendment["coordinate"] for amendment in amendments} == {
                        "registered-announcement", "correction-copy",
                    }
                if registration == "split":
                    assert disclosed["registered_table"] == "financial_reports"
                    assert amendments[0]["registered_table"] == "initial_reports"


@pytest.mark.parametrize(("repeated_lower", "late_registered_upper", "upper_is_first"),
                         [(11, False, True), (17, False, True), (11, True, True), (11, False, False)],
                         ids=["restated", "changed", "later-field-attestation", "explicitly-nonfirst"])
def test_surprise_uses_first_bound_from_each_verified_revision(
    tmp_path, repeated_lower, late_registered_upper, upper_is_first,
):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    first, = tables["financial_reports"]
    first.pop("parent_net_profit")
    first.update(announcement_ref="earnings-announcement", coordinate="registered-announcement",
                 available_at="2024-01-31T14:00:00+08:00", source_ref="first.pdf",
                 revision_id="v1", forecast_lower=11)
    corrected = dict(first, revision_id="v2", supersedes="v1", source_ref="corrected.pdf",
                     available_at="2024-01-31T18:00:00+08:00", forecast_lower=repeated_lower, forecast_upper=13)
    if not upper_is_first:
        corrected["field_evidence"] = {"forecast_upper": {"first_disclosure": False, "source_ref": "corrected.pdf"}}
    if late_registered_upper:
        first.update(forecast_upper=17, field_evidence={"forecast_upper": {
            "available_at": "2024-01-31T20:00:00+08:00", "announcement_ref": first["announcement_ref"],
        }})
    tables["initial_reports"] = [corrected]
    forecast, = tables["forecasts"]
    forecast["source_ref"] = "before.pdf"
    tables["forecasts"].append(dict(forecast, fact_key="after", forecast_net_profit=20,
                                   available_at="2024-01-31T16:00:00+08:00", source_ref="after.pdf"))
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    store, ref = local_fact_source(tmp_path / "local", tables)
    for hour in ((15, 19, 21) if late_registered_upper else (15, 19)):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                       compute_factor(store, ref, request)):
            if hour == 15 or not upper_is_first:
                assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
                assert result.gaps[0]["period"] == "2023Q4"
                proof, = result.gaps[0]["evidence"]
                assert (proof["forecast_lower"], proof["source_ref"]) == (11, "first.pdf")
                if hour == 15:
                    assert "corrected.pdf" not in json.dumps(result.gaps)
                else:
                    amendment, = proof["disclosure_revisions"]
                    assert (amendment["supersedes"], amendment["source_ref"]) == ("v1", "corrected.pdf")
                    assert amendment["field_evidence"]["forecast_upper"]["first_disclosure"] is False
                continue
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.20)
            assert [row["components"][key] for key in ("disclosed", "expected", "first_known_at", "released_at")] == [
                1_200_000_000, 1_000_000_000, first["available_at"], corrected["available_at"],
            ]
            report, expected = row["inputs"]
            for field, original in (("forecast_lower", first), ("forecast_upper", corrected)):
                quote = report["field_evidence"][field]
                assert (quote["source_ref"], quote["revision_id"], quote["available_at"]) == (
                    original["source_ref"], original["revision_id"], original["available_at"],
                )
            assert expected["source_ref"] == "before.pdf"


def test_surprise_import_address_is_not_publication_evidence(tmp_path):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    report = dict(tables["financial_reports"][0])
    report.pop("parent_net_profit")
    tables.pop("initial_reports", None)
    publication = "https://issuer.example/release/17"
    address = "vendor.example/data/financial_reports.csv"
    for name, locator, declared in (
        ("relative", address, False),
        ("absolute", f"https://{address}", False),
        ("download", "https://vendor.example/download?id=17", False),
        ("html-rendering", "input/aggregate-report.html", False),
        ("pdf-rendering", "input/aggregate-report.pdf", False),
        ("field-locator", f"https://{address}", False),
        ("original", f"https://{address}", True),
    ):
        original = dict(report, source_ref=locator)
        if declared:
            original["announcement_ref"] = publication
        lower = dict(original, coordinate="lower", forecast_lower=11, available_at="2024-01-31T14:00:00+08:00")
        upper = dict(original, coordinate="upper", forecast_upper=13, available_at="2024-01-31T18:00:00+08:00")
        if name == "field-locator":
            lower["field_evidence"] = {"forecast_lower": {"source_ref": locator}}
            upper["field_evidence"] = {"forecast_upper": {"source_ref": locator}}
        data = dict(tables, financial_reports=[lower, upper])
        (tmp_path / f"{source.case_id}.json").write_text(json.dumps(dict(doc, tables=data)))
        store, ref = local_fact_source(tmp_path / name, data)
        request = replace(query, cutoff=query.cutoff.replace(hour=19))
        for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                       compute_factor(store, ref, request)):
            if declared:
                assert not result.gaps and result.rows[0]["value"] == pytest.approx(.20)
                proof = result.rows[0]["inputs"][0]["field_evidence"]
                assert {proof[field]["announcement_ref"] for field in ("forecast_lower", "forecast_upper")} == {
                    publication,
                }
                assert {proof[field]["source_ref"] for field in ("forecast_lower", "forecast_upper")} == {locator}
            else:
                assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
                row, = result.gaps[0]["evidence"]
                assert (row["source_ref"], row["available_at"]) == (locator, lower["available_at"])


@pytest.mark.parametrize("declared", [False, True])
def test_surprise_distinguishes_field_originals_within_one_imported_row(tmp_path, declared):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    report = dict(tables["financial_reports"][0])
    report.pop("parent_net_profit")
    report.pop("announcement_ref", None)
    tables.pop("initial_reports", None)
    publication = "https://issuer.example/disclosure/17"
    lower = dict(source_ref="release-A.pdf", available_at="2024-01-31T14:00:00+08:00")
    upper = dict(source_ref="release-B.pdf", available_at="2024-01-31T18:00:00+08:00")
    if declared:
        lower["announcement_ref"] = upper["announcement_ref"] = publication
    report.update(source_ref="input/aggregate.html", available_at=lower["available_at"],
                  forecast_lower=11, forecast_upper=13,
                  field_evidence={"forecast_lower": lower, "forecast_upper": upper})
    tables["financial_reports"] = [report]
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    store, ref = local_fact_source(tmp_path / "local", tables)
    request = replace(query, cutoff=query.cutoff.replace(hour=19))
    for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                   compute_factor(store, ref, request)):
        if declared:
            assert not result.gaps and result.rows[0]["value"] == pytest.approx(.20)
            proof = result.rows[0]["inputs"][0]["field_evidence"]
            for field, original in (("forecast_lower", lower), ("forecast_upper", upper)):
                assert all(proof[field][key] == value for key, value in original.items())
        else:
            assert not result.rows and result.gaps[0]["field"] == "forecast_upper"


@pytest.mark.parametrize("registration", ["split", "duplicate-lower", "different-pdfs", "same-pdf", "later-actual",
                                          "import-raw", "import-input", "different-pdfs-shared-label",
                                          "same-pdf-shared-label"])
def test_surprise_groups_field_originals_across_registrations(tmp_path, registration):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    first = tables["financial_reports"][0]
    first.pop("parent_net_profit")
    lower = dict(first, available_at="2024-01-31T14:00:00+08:00", source_ref="guidance.pdf",
                 announcement_ref="guidance.pdf",
                 coordinate="lower", forecast_lower=11)
    upper = dict(first, available_at="2024-01-31T18:00:00+08:00", source_ref="guidance.pdf",
                 announcement_ref="guidance.pdf",
                 coordinate="upper", forecast_upper=13)
    tables["financial_reports"] = [lower, upper]
    tables["initial_reports"] = []
    if registration == "split":
        tables["financial_reports"] = [upper]
        tables["initial_reports"] = [lower]
    if registration == "duplicate-lower":
        tables["initial_reports"] = [dict(lower, coordinate="lower-copy")]
    if registration in ("import-raw", "import-input"):
        location = f"{registration.removeprefix('import-')}/factor_financial_reports/financial_reports.csv"
        lower["source_ref"] = upper["source_ref"] = location
        lower.pop("announcement_ref")
        upper.pop("announcement_ref")
    if registration.startswith(("different-pdfs", "same-pdf")):
        upper_original = "release-B.pdf" if registration.startswith("different-pdfs") else "release-A.pdf"
        lower.update(source_ref="input/financial-records.csv", field_evidence={"forecast_lower": {
            "source_ref": "release-A.pdf", "announcement_ref": "release-A.pdf",
        }})
        upper.update(source_ref="input/financial-records.csv", field_evidence={"forecast_upper": {
            "source_ref": upper_original, "announcement_ref": upper_original,
        }})
        if registration.endswith("-shared-label"):
            lower["announcement_ref"] = upper["announcement_ref"] = "notice-517"
    if registration == "later-actual":
        lower["forecast_upper"] = 13
        tables["financial_reports"] = [lower]
        tables["initial_reports"] = [dict(first, parent_net_profit=15, coordinate="later-actual",
                                          source_ref="actual.pdf", available_at=upper["available_at"])]
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    store, ref = local_fact_source(tmp_path / "local", tables)
    for hour in (15, 19):
        request = replace(query, cutoff=query.cutoff.replace(hour=hour))
        for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                       compute_factor(store, ref, request)):
            if registration.startswith(("different-pdfs", "import-")) or (
                hour == 15 and registration != "later-actual"
            ):
                assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
                assert "release-B.pdf" not in json.dumps(result.gaps)
                continue
            assert not result.gaps
            row, = result.rows
            assert row["value"] == pytest.approx(.20)
            assert row["components"]["first_known_at"] == lower["available_at"]
            stamps = (lower["available_at"], lower["available_at"] if registration == "later-actual"
                      else upper["available_at"])
            assert row["components"]["released_at"] == stamps[-1]
            original = "release-A.pdf" if registration.startswith("same-pdf") else "guidance.pdf"
            facts = row["inputs"][0].get("field_evidence", {})
            for field, stamp in zip(("forecast_lower", "forecast_upper"), stamps, strict=True):
                proof = facts.get(field, row["inputs"][0])
                assert proof["available_at"] == stamp and proof["source_ref"] == original
            assert "actual.pdf" not in json.dumps(row)


@pytest.mark.parametrize("second_notice", ["duplicate", "different-notice", "different-document"])
def test_surprise_reconciles_first_release_in_selected_library(tmp_path, second_notice):
    from test_factor_recheck import library

    _, document, query = example_query("earnings_surprise")
    tables = document["tables"]
    tables.pop("initial_reports")
    first, = tables["financial_reports"]
    first.update(coordinate="notice-first", announcement_ref="issuer-A-Q4-notice-517",
                 source_ref="exchange/issuer-A/q4/notice-517.pdf")
    other = dict(first)
    if second_notice == "different-notice":
        other["announcement_ref"] = "issuer-A-independent-notice-218"
    if second_notice == "different-document":
        other["source_ref"] = "exchange/issuer-A/q4/independent-218.pdf"
    store, ref = local_fact_source(tmp_path / "first", tables)
    alternate = local_fact_source(tmp_path / "second", {"financial_reports": [other]})
    base = compute_factor(store, library(store, (store, ref)), query)
    assert not base.gaps and base.rows[0]["value"] == pytest.approx(.20)
    result = compute_factor(store, library(store, (store, ref), alternate), query)
    if second_notice != "duplicate":
        assert not result.rows and result.gaps[0]["field"] == "initial_reports"
        assert {(entry["announcement_ref"], entry["source_ref"]) for entry in result.gaps[0]["evidence"]} == {
            (first["announcement_ref"], first["source_ref"]), (other["announcement_ref"], other["source_ref"]),
        }
        return
    assert not result.gaps
    row, = result.rows
    assert row["value"] == pytest.approx(.20) and row["components"] == base.rows[0]["components"]
    originals = row["inputs"][0]["field_evidence"]["parent_net_profit"]["corroborating_sources"]
    assert {item["source_root"] for item in originals} == {str(store.root), str(alternate[0].root)}
    assert {item["source_ref"] for item in originals} == {first["source_ref"]}


@pytest.mark.parametrize("copied", [
    "none", "lower", "both", "different-upper", "lower-other-table", "different-upper-other-table",
])
def test_surprise_corroborates_partial_registration_in_selected_library(tmp_path, copied):
    from test_factor_recheck import library

    _, document, query = example_query("earnings_surprise")
    tables = document["tables"]
    tables.pop("initial_reports")
    original, = tables["financial_reports"]
    original.pop("parent_net_profit")
    original.update(announcement_ref="notice-517", source_ref="exchange/issuer-A/q4/notice-517.pdf")
    lower = dict(original, coordinate="lower", forecast_lower=11, available_at="2024-01-31T14:00:00+08:00")
    upper = dict(original, coordinate="upper", forecast_upper=13, available_at="2024-01-31T18:00:00+08:00")
    primary = local_fact_source(tmp_path / "primary", dict(tables, financial_reports=[lower, upper]))
    registrations = [lower]
    alternate_table = "initial_reports" if copied.endswith("-other-table") else "financial_reports"
    if copied == "both":
        registrations.append(upper)
    elif copied.startswith("different-upper"):
        registrations.append(dict(upper, forecast_upper=15, source_ref="independent-release.pdf"))
    alternate = local_fact_source(tmp_path / "alternate", {alternate_table: registrations})
    selections = [(primary,)] if copied == "none" else [(primary, alternate), (alternate, primary)]
    store = primary[0]
    for assets in selections:
        selected = library(store, *assets)
        early = compute_factor(store, selected, query)
        assert not early.rows and early.gaps[0]["field"] == "forecast_upper"
        assert upper["available_at"] not in json.dumps(early.gaps)

        result = compute_factor(store, selected, replace(query, cutoff=query.cutoff.replace(hour=19)))
        if copied.startswith("different-upper"):
            assert not result.rows and result.gaps[0]["field"] == "initial_reports"
            assert {entry["forecast_upper"] for entry in result.gaps[0]["evidence"]} == {13, 15}
            continue

        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.20)
        assert row["components"]["first_known_at"] == lower["available_at"]
        assert row["components"]["released_at"] == upper["available_at"]
        assert row["components"]["expected"] == 1_000_000_000
        assert row["components"]["disclosed"] == 1_200_000_000
        disclosure, forecast = row["inputs"]
        assert forecast["available_at"] < lower["available_at"]
        for field, original in (("forecast_lower", lower), ("forecast_upper", upper)):
            proof = disclosure["field_evidence"][field]
            sources = proof.get("corroborating_sources", [proof])
            expected_roots = {str(store.root)}
            if copied == "both" or (copied.startswith("lower") and field == "forecast_lower"):
                expected_roots.add(str(alternate[0].root))
            assert {entry["source_root"] for entry in sources} == expected_roots
            assert {entry["source_ref"] for entry in sources} == {original["source_ref"]}
            assert {entry["coordinate"] for entry in sources} == {original["coordinate"]}
            if copied == "lower-other-table":
                fact_key = f"financial_reports|A|{original['coordinate']}"
                expected = {(primary[1].snapshot_id, "financial_reports", fact_key)}
                if field == "forecast_lower":
                    expected.add((alternate[1].snapshot_id, "initial_reports", "initial_reports|A|lower"))
                assert len(sources) == len(expected)
                assert {
                    (entry["snapshot_id"], entry["registered_table"], entry["fact_key"]) for entry in sources
                } == expected


@pytest.mark.parametrize("registration", ["same-import", "different-import", "issuer-publication"])
def test_surprise_requires_shared_publication_for_partial_assets(tmp_path, registration):
    from test_factor_recheck import library

    _, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    tables.pop("initial_reports")
    first, = tables["financial_reports"]
    first.pop("parent_net_profit")
    publication = "https://issuer.example/A/2023Q4/notice-17"
    lower = dict(first, coordinate="import-row-18", source_ref="input/financial-reports.csv",
                 available_at="2024-01-31T14:00:00+08:00", forecast_lower=11, announcement_ref="notice-17")
    upper = dict(first, coordinate="import-row-27", source_ref=lower["source_ref"],
                 available_at="2024-01-31T18:00:00+08:00", forecast_upper=13, announcement_ref="notice-17")
    if registration == "different-import":
        upper["source_ref"] = "input/other-import.csv"
    if registration == "issuer-publication":
        lower["announcement_ref"] = upper["announcement_ref"] = publication
    primary = local_fact_source(tmp_path / "left", dict(tables, financial_reports=[lower]))
    alternate = local_fact_source(tmp_path / "right", {"financial_reports": [upper]})
    request = replace(query, cutoff=query.cutoff.replace(hour=19))
    for assets in ((primary, alternate), (alternate, primary)):
        result = compute_factor(primary[0], library(primary[0], *assets), request)
        if registration != "issuer-publication":
            assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
            evidence, = result.gaps[0]["evidence"]
            assert evidence["coordinate"] == lower["coordinate"]
            assert evidence["source_root"] == str(primary[0].root)
            continue
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.20)
        assert row["components"]["expected"] == 1_000_000_000
        fields = row["inputs"][0]["field_evidence"]
        for field, asset in (("forecast_lower", primary), ("forecast_upper", alternate)):
            assert fields[field]["source_root"] == str(asset[0].root)
            assert fields[field]["announcement_ref"] == publication


@pytest.mark.parametrize("field_source", ["default", "repeated-import", "separate-announcement"])
def test_surprise_keeps_row_announcement_when_field_repeats_import(tmp_path, field_source):
    source, doc, query = example_query("earnings_surprise")
    tables = doc["tables"]
    tables.pop("initial_reports")
    first, = tables["financial_reports"]
    first.pop("parent_net_profit")
    publication = "https://issuer.example/A/2023Q4/notice-17"
    lower = dict(first, coordinate="import-row-18", source_ref="input/batch1.csv",
                 available_at="2024-01-31T14:00:00+08:00", forecast_lower=11, announcement_ref=publication)
    upper = dict(first, coordinate="import-row-27", source_ref="input/batch2.csv",
                 available_at="2024-01-31T18:00:00+08:00", forecast_upper=13, announcement_ref=publication)
    if field_source != "default":
        evidence = {"source_ref": upper["source_ref"]}
        if field_source == "separate-announcement":
            evidence["announcement_ref"] = "https://issuer.example/A/2023Q4/another-notice"
        upper["field_evidence"] = {"forecast_upper": evidence}
    tables["financial_reports"] = [lower, upper]
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(doc))
    store, ref = local_fact_source(tmp_path / "local", tables)
    request = replace(query, cutoff=query.cutoff.replace(hour=19))
    for result in (compute_factor(None, TeachingSource(source.case_id, tmp_path), request),
                   compute_factor(store, ref, request)):
        if field_source == "separate-announcement":
            assert not result.rows and result.gaps[0]["field"] == "forecast_upper"
            continue
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.20)
        assert row["components"]["expected"] == 1_000_000_000
        fields = row["inputs"][0]["field_evidence"]
        for field, original in (("forecast_lower", lower), ("forecast_upper", upper)):
            assert fields[field]["announcement_ref"] == publication
            assert fields[field]["source_ref"] == original["source_ref"]
            assert fields[field]["available_at"] == original["available_at"]


def test_surprise_matches_published_predecessor_across_selected_assets(tmp_path):
    from test_factor_recheck import library

    _, document, query = example_query("earnings_surprise")
    tables = document["tables"]
    tables.pop("initial_reports")
    first, = tables["financial_reports"]
    first.update(announcement_ref="https://exchange.example/issuer-A/q4/notice-517",
                 coordinate="release", revision_id="v1", source_ref="first.pdf")
    correction = dict(first, coordinate="corrected", revision_id="v2", supersedes="v1", source_ref="corrected.pdf",
                      parent_net_profit=13, available_at="2024-01-31T18:00:00+08:00")
    store, ref = local_fact_source(tmp_path / "first", tables)
    newer = local_fact_source(tmp_path / "newer", {"initial_reports": [correction]})
    different = dict(correction, announcement_ref="https://exchange.example/issuer-A/independent/notice-517")
    unrelated = local_fact_source(tmp_path / "unrelated", {"initial_reports": [different]})
    request = replace(query, cutoff=query.cutoff.replace(hour=19))
    for candidate in (newer, unrelated):
        result = compute_factor(store, library(store, (store, ref), candidate), request)
        assert not result.gaps
        row, = result.rows
        assert row["value"] == pytest.approx(.20)
        assert row["components"]["first_known_at"] == first["available_at"]
        amendments = row["inputs"][0].get("disclosure_revisions", ())
        if candidate == newer:
            revision, = amendments
            assert revision["source_root"] == str(newer[0].root)
            assert revision["supersedes"] == "v1" and revision["source_ref"] == "corrected.pdf"
        else:
            assert not amendments


def test_forecast_activity_recovers_old_versions_at_lookback_start(tmp_path):
    source, document, query = example_query("earnings_revision")
    for row in document["tables"]["forecasts"]:
        row["fact_key"] = f"{row['institution']}|{row['period']}"
        if row["available_at"].startswith("2024-01-10"):
            row["revision_id"] = "old"
        else:
            row.update(revision_id="new", supersedes="old")
    path = tmp_path / f"{source.case_id}.json"
    path.write_text(json.dumps(document))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    assert result.rows
    assert result.rows[0]["value"] == pytest.approx(1 / 60)


def test_drift_starts_same_session_for_preopen_announcements(tmp_path):
    source, document, query = example_query("post_announcement_drift")
    document["tables"]["initial_reports"][0]["available_at"] = "2024-01-29T08:00:00+08:00"
    (tmp_path / f"{source.case_id}.json").write_text(json.dumps(document))
    result = compute_factor(None, TeachingSource(source.case_id, tmp_path), query)
    assert result.rows[0]["value"] == pytest.approx((11 / 8 - 1) - (102 / 90 - 1))
    assert result.rows[0]["components"]["start"] == "2024-01-29"
