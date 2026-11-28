from dataclasses import replace
from datetime import date

import pytest

from factor_cases import example_query, local_fact_source, local_volume_source
from hetu_quant.factors import close_cutoff, compute_factor


@pytest.mark.parametrize("allowed", [True, False])
def test_preview_routes_by_rights_and_dependencies(tmp_path, allowed):
    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import preview_inputs

    _, document, request = example_query("earnings_yield")
    store, source = local_fact_source(tmp_path, {"market_cap": document["tables"]["market_cap"]}, allowed=allowed)
    preview = preview_inputs(store, build_trial(source, request))
    assert preview.can_submit is allowed
    assert ("pending" if allowed else "denied") in {check["state"] for check in preview.checks}


def test_evidence_obeys_cutoff_and_keeps_early_registration():
    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import visible_evidence

    source, _, request = example_query("earnings_yield")
    early = build_trial(source, request)
    late = build_trial(source, replace(request, cutoff=request.cutoff.replace(hour=19)))
    early_result = compute_factor(None, source, early.request)
    late_result = compute_factor(None, source, late.request)
    view = visible_evidence(None, early, early_result)
    assert view["rows"][0]["value"] == pytest.approx(0.06)
    assert "2024-01-31T18:00" not in str(view)
    assert visible_evidence(None, late, late_result)["rows"][0]["value"] == pytest.approx(0.08)
    with pytest.raises(ValueError):
        visible_evidence(None, early, late_result)


def test_evidence_rechecks_rights_and_download_scope(tmp_path):
    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import visible_evidence

    store, ref = local_volume_source(tmp_path, code="A.SH")
    _, _, request = example_query("traded_amount")
    day = date(2024, 4, 1)
    trial = build_trial(ref, replace(request, subjects=("A.SH",), trade_date=day, cutoff=close_cutoff(day)))
    result = compute_factor(store, ref, trial.request)
    assert visible_evidence(store, trial, result)["rows"][0]["value"] == 15_000_000
    assert not visible_evidence(store, trial, result, row_index=0, operation="download")["permitted"]
    for product in ("factor_daily", "factor_calendar", "factor_proofs"):
        store.config["products"][product]["permission"]["allow_export"] = True
    exported = visible_evidence(store, trial, result, row_index=0, operation="download")
    assert exported["permitted"] and exported["item"]["value"] == 15_000_000
    store.config["products"]["factor_daily"]["permission"]["allowed_uses"] = ["daily_archive"]
    assert not visible_evidence(store, trial, result)["rows"]
    assert result.rows[0]["value"] == 15_000_000


def test_mixed_products_keep_the_authorized_amount(tmp_path, monkeypatch):
    import json

    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import preview_inputs

    store, ref = local_volume_source(tmp_path, code="A.SH")
    manifest = store.load_manifest(ref)
    allowed = next(entry for entry in manifest["table_files"] if entry["table"] == "daily_bars")
    manifest["table_files"].append(dict(allowed, product_id="restricted_daily", path="should-not-open.parquet"))
    store.config["products"]["restricted_daily"] = json.loads(json.dumps(store.product("factor_daily")))
    store.config["products"]["restricted_daily"]["permission"]["allowed_uses"] = ["daily_archive"]
    manifest["items"].append({"table": "daily_bars", "product_id": "restricted_daily", "source": {}})
    real_manifest = store.load_manifest
    monkeypatch.setattr(store, "load_manifest", lambda current: manifest if current == ref else real_manifest(current))
    real_range = store.read_range
    reads = []

    def record(*args, **kwargs):
        reads.append(kwargs)
        return real_range(*args, **kwargs)

    monkeypatch.setattr(store, "read_range", record)
    _, _, request = example_query("traded_amount")
    day = date(2024, 4, 1)
    trial = build_trial(ref, replace(request, subjects=("A.SH",), trade_date=day, cutoff=close_cutoff(day)))
    assert preview_inputs(store, trial).can_submit
    result = compute_factor(store, ref, trial.request)
    assert result.rows[0]["value"] == 15_000_000
    daily = [read for read in reads if read["product_id"] == "factor_daily"]
    assert daily and all(read["start"] == read["end"] == day and "close_raw" not in read["columns"] for read in daily)
    assert not any(read["product_id"] == "restricted_daily" for read in reads)


def test_dependency_preview_uses_formula_window(tmp_path):
    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import preview_inputs

    store, ref = local_volume_source(tmp_path, code="A.SH")
    _, _, momentum = example_query("price_momentum")
    day = date(2024, 4, 1)
    for skip, expected_end in ((1, "2024-03-29"), (0, "2024-04-01")):
        trial = build_trial(
            ref,
            replace(
                momentum,
                subjects=("A.SH",),
                trade_date=day,
                cutoff=close_cutoff(day),
                parameters={"window": 1, "skip": skip},
            ),
        )
        coverage = preview_inputs(store, trial)
        checks = [check for check in coverage.checks if check["field"] == "行情日期"]
        assert checks and checks[0]["end"].isoformat() == expected_end


def test_revoked_asset_does_not_mask_independent_row(tmp_path):
    import json

    from hetu_data.library import publish_library

    from hetu_quant.factors.workbench import build_trial
    from hetu_quant.factors.workbench_inputs import visible_evidence

    store, left = local_volume_source(tmp_path / "left", code="A.SH")
    right_store, right = local_volume_source(tmp_path / "right", code="B.SH")
    lib = publish_library(
        store,
        asset_snapshots=tuple(
            dict(
                root=str(owner.root),
                snapshot_id=ref.snapshot_id,
                tables=sorted({entry["table"] for entry in owner.load_manifest(ref)["table_files"]}),
            )
            for owner, ref in ((store, left), (right_store, right))
        ),
    )
    _, _, request = example_query("traded_amount")
    day = date(2024, 4, 1)
    trial = build_trial(lib, replace(request, subjects=("A.SH", "B.SH"), trade_date=day, cutoff=close_cutoff(day)))
    result = compute_factor(store, lib, trial.request)
    assert [row["value"] for row in result.rows] == [15_000_000, 15_000_000]
    right_cfg = right_store.root / "data-products.json"
    doc = json.loads(right_cfg.read_text())
    doc["products"]["factor_daily"]["permission"]["allowed_uses"] = ["daily_archive"]
    right_cfg.write_text(json.dumps(doc))
    view = visible_evidence(store, trial, result)
    assert view["status"] == "partial"
    assert [(row["subject"], row["value"]) for row in view["rows"]] == [("A.SH", 15_000_000)]
    assert len(result.rows) == 2
