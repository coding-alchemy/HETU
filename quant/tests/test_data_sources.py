"""G2付费许可、真实标准化及生产证据；网络仅用合成传输替换。"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hetu_quant.data import AccessDenied, DataStore
from hetu_quant.models import DataRequest, data_request_to_dict
from source_cases import SourceClient, response

CN = ZoneInfo("Asia/Shanghai")


def provider_module():
    from hetu_quant.providers import tushare

    return tushare


def request(product="ts_a_daily", start="2026-01-05", end="2026-01-06", mode="paid", **kwargs):
    return DataRequest(
        (product,),
        ("SYNTH.SH",),
        date.fromisoformat(start),
        date.fromisoformat(end),
        datetime(2026, 10, 25, 9, tzinfo=CN),
        "backtest_low_volatility",
        mode=mode,
        **kwargs,
    )


def grant(product="ts_a_daily", **changes):
    row = dict(
        product_id=product,
        approved_scope_ref="synthetic-G2",
        license_ref="synthetic-personal-license",
        allowed_operations=["read", "import", "acquire"],
        markets=["SH"],
        codes=["SYNTH.SH"],
        start="2000-01-01",
        end="2099-12-31",
        acquire_until="2099-12-31",
        retained_use=False,
    )
    return {**row, **changes}


def store(tmp_path, client=None, product="ts_a_daily", **grant_changes):
    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    (root / "data-entitlements.json").write_text(json.dumps({"grants": [grant(product, **grant_changes)]}))
    adapter = provider_module().TushareProvider(client=client or SourceClient())
    return DataStore(root, providers={"tushare": adapter}), adapter


def test_free_mode_never_reaches_paid_client(tmp_path):
    ds, adapter = store(tmp_path)
    with pytest.raises(AccessDenied):
        ds.prepare(request(mode="free"))
    assert adapter.client.calls == []


def test_paid_grant_is_product_and_operation_scoped():
    from hetu_quant.data_access import check_product_access

    p = {"product_id": "ts_a_daily", "provider": "tushare", "fee_class": "paid"}
    check_product_access("paid", p, grant(), operation="acquire")
    with pytest.raises(AccessDenied):
        check_product_access("free", p, grant(), operation="acquire")
    with pytest.raises(AccessDenied):
        check_product_access("paid", p, grant("ts_a_market_cap"), operation="acquire")
    with pytest.raises(AccessDenied):
        check_product_access("paid", p, grant(allowed_operations=["read"]), operation="import")
    check_product_access("paid", p, grant(acquire_until="2000-01-01", retained_use=True), operation="read")
    with pytest.raises(AccessDenied):
        check_product_access("paid", p, grant(acquire_until="2000-01-01"), operation="acquire")


def test_paid_grant_rejects_other_code_and_period(tmp_path):
    ds, adapter = store(tmp_path, start="2026-01-06")
    with pytest.raises(AccessDenied):
        ds.prepare(request())
    assert adapter.client.calls == []
    ds, adapter = store(tmp_path, codes=["OTHER.SH"])
    with pytest.raises(AccessDenied):
        ds.prepare(request())
    assert adapter.client.calls == []


def test_selected_provider_units_and_raw_prices():
    adapter = provider_module().TushareProvider(client=SourceClient())
    raw = adapter.decode_response(
        response(), api="daily", params={"ts_code": "SYNTH.SH"}, obtained_at="2026-10-25T09:00:00+08:00"
    )
    bars = adapter.standardize("ts_a_daily", raw, {})["daily_bars"]
    row = bars.iloc[0]
    assert row.close_raw == 10
    assert row.volume_shares == 1234
    assert row.amount_cny == 56780
    assert row.provider_pre_close == 9
    assert "pre_close_raw" not in bars
    assert pd.isna(row.close_total_return)


def test_selected_provider_action_dates_and_coverage():
    adapter = provider_module().TushareProvider(client=SourceClient("dividend"))
    raw = adapter.decode_response(
        response("dividend"), api="dividend", params={"ts_code": "SYNTH.SH"}, obtained_at="2026-10-25T09:00:00+08:00"
    )
    actions = adapter.standardize("ts_a_actions", raw, {})["corporate_actions"]
    split = actions[actions.action_type.eq("bonus_split")].iloc[0]
    assert split.m == 2
    assert split.record_date == "2026-01-04"
    assert split.ex_date == "2026-01-05"
    assert split.pay_date == "2026-01-07"
    assert split.stock_listing_date == "2026-01-09"
    assert split.available_at == "2026-10-25T09:00:00+08:00"
    assert all(x.get("basis") != "ex_date" for x in raw.attrs["query_evidence"])


def test_selected_provider_versions_are_not_latest_only():
    adapter = provider_module().TushareProvider(client=SourceClient("dividend"))
    data = json.loads(response("dividend"))
    original = list(data["data"]["items"][0])
    original[3], original[5], original[10] = "预案", 0.55, None
    data["data"]["items"].insert(0, original)
    raw = adapter.decode_response(
        json.dumps(data).encode(),
        api="dividend",
        params={"ts_code": "SYNTH.SH"},
        obtained_at="2026-10-25T09:00:00+08:00",
    )
    versions = adapter.standardize("ts_action_versions", raw, {})["action_versions"]
    assert versions.div_proc.tolist() == ["预案", "实施"]
    assert versions.cash_div_tax.tolist() == [0.55, 0.5]
    assert versions.revision_id.nunique() == 2
    # G1未证明修订公开关系，不能用ann_date使这些后来取得的版本在历史信号可用。
    assert versions.published_at.isna().all()


def test_provider_proofs_survive_publish_copy_merge_reopen(tmp_path):
    ds, adapter = store(tmp_path)
    one = ds.prepare(request(end="2026-01-05"))
    m = ds.load_manifest(one)
    originals = [x for i in m["items"] for x in i["raw_files"] if x["path"].endswith(".json")]
    assert len(originals) == 1
    original_bytes = (one.root / "snapshots" / one.snapshot_id / originals[0]["path"]).read_bytes()
    assert json.loads(original_bytes)["data"]["items"][0][0] == "SYNTH.SH"
    two = ds.prepare(request(end="2026-01-06"))
    reopened = DataStore(ds.root, providers={"tushare": adapter})
    calls = len(adapter.client.calls)
    again = reopened.prepare(request(end="2026-01-06"))
    assert len(adapter.client.calls) == calls
    assert again.snapshot_id == two.snapshot_id
    evidence = reopened.read_table(two, "knowledge_evidence")
    assert len(evidence)
    assert not evidence.verified.astype(bool).any()
    assert evidence.published_at.isna().all()
    assert (one.root / "snapshots" / one.snapshot_id / originals[0]["path"]).read_bytes() == original_bytes
    assert all((ds.root / "evidence" / name).exists() for name in evidence.evidence_ref)


def test_import_cannot_disguise_paid_source_as_free(tmp_path):
    ds, _ = store(tmp_path)
    data = tmp_path / "bars.csv"
    data.write_text("code,date,close_raw\nSYNTH.SH,2026-01-05,10\n")
    manifest = tmp_path / "import.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "free",
                "items": [
                    {
                        "product_id": "ts_a_daily",
                        "table": "daily_bars",
                        "files": [data.name],
                        "source": {
                            "name": "local_file",
                            "fee_class": "free",
                            "permission_basis": "synthetic",
                            "obtained_at": "2026-10-25T00:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    with pytest.raises(AccessDenied):
        ds.import_files(manifest, (data,))


def test_provider_json_import_uses_same_mapping_and_cache_without_network(tmp_path):
    ds, adapter = store(tmp_path, allowed_operations=["import", "read"])
    original = tmp_path / "response.json"
    original.write_bytes(response())
    req = request(end="2026-01-05")
    manifest = tmp_path / "import.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "paid",
                "request": data_request_to_dict(req),
                "items": [
                    {
                        "product_id": "ts_a_daily",
                        "table": "daily_bars",
                        "files": [original.name],
                        "format": "provider_json",
                        "query": {
                            "api": "daily",
                            "params": {"ts_code": "SYNTH.SH", "start_date": "20260105", "end_date": "20260105"},
                        },
                        "request": data_request_to_dict(req),
                        "source": {
                            "name": "tushare",
                            "fee_class": "paid",
                            "permission_basis": "synthetic current-use",
                            "obtained_at": "2026-10-25T09:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    ref = ds.import_files(manifest, (original,))
    assert ds.read_table(ref, "daily_bars").iloc[0].volume_shares == 1234
    again = ds.prepare(req)
    assert again.snapshot_id == ref.snapshot_id
    assert adapter.client.calls == []
    assert any(e["path"].endswith(".json") for i in ds.load_manifest(ref)["items"] for e in i["raw_files"])


def test_read_and_import_reject_expired_without_retained_license(tmp_path):
    ds, _ = store(tmp_path)
    ref = ds.prepare(request(end="2026-01-05"))
    path = ds.root / "data-entitlements.json"
    path.write_text(json.dumps({"grants": [grant(acquire_until="2000-01-01")]}))
    with pytest.raises(AccessDenied):
        ds.read_table(ref, "daily_bars")
    path.write_text(json.dumps({"grants": [grant(acquire_until="2000-01-01", retained_use=True)]}))
    assert len(ds.read_table(ref, "daily_bars")) == 1


def test_free_mode_cannot_reuse_paid_snapshot_with_free_request_ids(tmp_path):
    from dataclasses import replace

    ds, _ = store(tmp_path)
    ref = ds.prepare(request(end="2026-01-05"))
    free_request = replace(request(end="2026-01-05", mode="free"), product_ids=("cn_a_daily",))
    with pytest.raises(AccessDenied):
        ds.qualify(free_request, ref)


def test_unknown_provider_history_cannot_pass_prepare_backtest(tmp_path):
    from backtest_cases import case_definition, make_backtest_case
    from hetu_quant.backtest_data import prepare_backtest

    ds, _ = store(tmp_path)
    ref = ds.prepare(request(end="2026-01-05"))
    definition = case_definition(make_backtest_case("history"), ref.snapshot_id, mode="paid")
    report = prepare_backtest(definition, ds, tmp_path / "bundle")
    assert not report.can_run


def test_page_limit_and_failure_do_not_claim_complete(tmp_path):
    from source_cases import response

    ds, adapter = store(tmp_path)
    payload = json.loads(response())
    payload["data"]["has_more"] = True
    adapter.client.query = lambda *_: json.dumps(payload).encode()
    with pytest.raises(ValueError, match="pagination"):
        ds.prepare(request())
    assert ds.plan(request())["segments"]


def test_read_cannot_exceed_current_object_grant(tmp_path):
    ds, _ = store(tmp_path)
    ref = ds.prepare(request(end="2026-01-05"))
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": [grant(codes=["OTHER.SH"])]}))
    with pytest.raises(AccessDenied):
        ds.read_table(ref, "daily_bars")


def test_dated_empty_query_reuses_proof_without_fabricating_bars(tmp_path):
    ds, adapter = store(tmp_path, SourceClient(empty=True))
    first = ds.prepare(request())
    assert ds.read_table(first, "daily_bars").empty
    again = ds.prepare(request())
    assert len(adapter.client.calls) == 1
    assert again.snapshot_id == first.snapshot_id


def test_catalog_response_keeps_raw_but_normalizes_only_requested_objects(tmp_path):
    class Catalog:
        def query(self, api, params, fields):
            return json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "trade_date", "name", "total_share"],
                        "items": [
                            ["SYNTH.SH", "20260105", "synthetic", 100],
                            ["OTHER.SH", "20260105", "other synthetic", 200],
                        ],
                        "has_more": False,
                    },
                }
            ).encode()

    ds, _ = store(tmp_path, Catalog(), product="ts_bak_basic")
    ref = ds.prepare(request("ts_bak_basic", end="2026-01-05"))
    assert ds.read_table(ref, "security_observations").code.tolist() == ["SYNTH.SH"]
    raws = [r for i in ds.load_manifest(ref)["items"] for r in i["raw_files"] if r["path"].endswith(".json")]
    assert (
        len(json.loads((ds.root / "snapshots" / ref.snapshot_id / raws[0]["path"]).read_text())["data"]["items"]) == 2
    )


def _provider_import_items(tmp_path, product, api, bodies, *, base_ids=()):
    req = request(product, end="2026-01-05")
    files, items = [], []
    for index, body in enumerate(bodies):
        path = tmp_path / f"provider-{index}.json"
        path.write_bytes(body)
        files.append(path)
        items.append(
            {
                "product_id": product,
                "table": "market_cap",
                "files": [path.name],
                "format": "provider_json",
                "request": data_request_to_dict(req),
                "query": {
                    "api": api,
                    "params": {"ts_code": "SYNTH.SH", "start_date": "20260105", "end_date": "20260105"},
                },
                "source": {
                    "name": "tushare",
                    "fee_class": "paid",
                    "permission_basis": "synthetic G2 license",
                    "obtained_at": "2026-10-25T09:00:00+08:00",
                },
            }
        )
    manifest = tmp_path / "provider-import.json"
    manifest.write_text(
        json.dumps(
            {"mode": "paid", "request": data_request_to_dict(req), "base_snapshot_ids": list(base_ids), "items": items}
        )
    )
    return manifest, tuple(files)


def test_same_batch_provider_versions_same_month_survive_copy(tmp_path):
    ds, _ = store(tmp_path, product="ts_a_market_cap")
    original = response("daily_basic")
    revised = json.loads(original)
    revised["data"]["items"][0][4] = 1100
    manifest, files = _provider_import_items(
        tmp_path, "ts_a_market_cap", "daily_basic", [original, json.dumps(revised).encode()]
    )
    ref = ds.import_files(manifest, files)
    assert sorted(ds.read_table(ref, "market_cap").market_cap_cny.tolist()) == [10000000, 11000000]
    again = ds.prepare(request("ts_a_market_cap", end="2026-01-05"))
    assert len(ds.read_table(again, "market_cap")) == 2
    expanded = ds.prepare(request("ts_a_market_cap"))
    rows = ds.read_table(expanded, "market_cap")
    assert len(rows[rows.date.eq("2026-01-05")]) == 2


def test_provider_import_inherits_year_file_and_object_coverage(tmp_path):
    ds, _ = store(tmp_path)
    first = ds.prepare(request())
    before = ds.load_manifest(first)
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": [grant(), grant("ts_a_market_cap")]}))
    manifest, files = _provider_import_items(
        tmp_path, "ts_a_market_cap", "daily_basic", [response("daily_basic")], base_ids=[first.snapshot_id]
    )
    new = ds.import_files(manifest, files)
    after = ds.load_manifest(new)
    assert before["object_coverage"] == after["object_coverage"]
    pd.testing.assert_frame_equal(ds.read_table(first, "daily_bars"), ds.read_table(new, "daily_bars"))
    assert not ds.plan(request())["segments"]


@pytest.mark.parametrize("code,day", [("OTHER.SH", "2026-01-05"), ("SYNTH.SH", "1999-01-05")])
def test_paid_csv_outside_grant_is_rejected_before_publication(tmp_path, code, day):
    ds, _ = store(tmp_path, product="ts_a_market_cap")
    csv = tmp_path / "outside.csv"
    pd.DataFrame([dict(code=code, date=day, market_cap_cny=100, available_at="2026-10-25", revision_id="fact")]).to_csv(
        csv, index=False
    )
    manifest = tmp_path / "outside.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "paid",
                "items": [
                    {
                        "product_id": "ts_a_market_cap",
                        "table": "market_cap",
                        "files": [csv.name],
                        "source": {
                            "name": "tushare",
                            "fee_class": "paid",
                            "permission_basis": "synthetic",
                            "obtained_at": "2026-10-25T09:00:00+08:00",
                        },
                    }
                ],
            }
        )
    )
    with pytest.raises(AccessDenied):
        ds.import_files(manifest, (csv,))
    assert list((ds.root / "snapshots").iterdir()) == []


def test_missing_schema_cannot_be_success_empty_coverage(tmp_path):
    ds, adapter = store(tmp_path)
    adapter.client.query = lambda *_: b'{"code":0,"data":{"fields":[],"items":[],"has_more":false}}'
    with pytest.raises(ValueError, match="fields|schema"):
        ds.prepare(request())
    assert ds.plan(request())["segments"]


def test_calendar_has_no_stock_object_domain(tmp_path):
    class Calendar:
        def query(self, api, params, fields):
            return (
                b'{"code":0,"data":{"fields":["cal_date","is_open"],'
                b'"items":[["20260105",1],["20260106",1]],"has_more":false}}'
            )

    ds, _ = store(tmp_path, Calendar(), product="ts_trade_calendar")
    ref = ds.prepare(request("ts_trade_calendar"))
    assert len(ds.read_table(ref, "calendar")) == 2


def test_bak_basic_uses_hundred_million_shares_not_daily_basic_ten_thousand():
    adapter = provider_module().TushareProvider(client=object())
    body = json.dumps(
        {
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_date", "name", "total_share"],
                "items": [["SYNTH.SH", "20260105", "synthetic", 12.5]],
                "has_more": False,
            },
        }
    ).encode()
    raw = adapter.decode_response(
        body, api="bak_basic", params={"trade_date": "20260105"}, obtained_at="2026-10-25T09:00:00+08:00"
    )
    observed = adapter.standardize("ts_bak_basic", raw, {})["security_observations"].iloc[0]
    assert observed.total_share == 12.5
    assert observed.total_shares == 1250000000


def test_directory_raw_observation_import_is_codeless_and_not_historical_proof(tmp_path):
    ds, _ = store(tmp_path, product="ts_a_basic", codes=["SYNTH.SH", "SYNTH.SZ"])
    bodies = [response("stock_basic"), response("stock_basic", code="SYNTH.SZ"),
              response("stock_basic", empty=True)]
    manifest, files = _provider_import_items(tmp_path, "ts_a_basic", "stock_basic", bodies)
    doc = json.loads(manifest.read_text())
    for item, exchange, status in zip(doc["items"], ["SSE", "SZSE", "BSE"], ["L", "L", "P"], strict=True):
        item["table"] = "securities"
        item["query"]["params"] = {"exchange": exchange, "list_status": status}
        item["request"]["codes"] = []
        item["request"]["start"] = item["request"]["end"] = "2026-10-25"
    manifest.write_text(json.dumps(doc))
    # 标准层按实际行对象检查权限，而非把目录市场当成一只股票。
    grants = ds.root / "data-entitlements.json"
    permissions = json.loads(grants.read_text())
    permissions["grants"][0]["markets"] = ["SH", "SZ", "BJ"]
    grants.write_text(json.dumps(permissions))
    ref = ds.import_files(manifest, files)
    assert sorted(ds.read_table(ref, "securities").code) == ["SYNTH.SH", "SYNTH.SZ"]
    assert "historical_securities" not in {e["table"] for e in ds.load_manifest(ref)["table_files"]}
    evidence = ds.read_table(ref, "knowledge_evidence")
    assert len(evidence) == 2 and not evidence.verified.any()
    assert evidence.published_at.isna().all()
    assert len(ds.load_manifest(ref)["items"]) == 3
    assert not ds.load_manifest(ref).get("object_coverage")


def test_directory_b_shares_are_not_classified_as_common_a_stocks():
    adapter = provider_module().TushareProvider(client=object())
    raw = adapter.decode_response(response("stock_basic", code="900999.SH"), api="stock_basic",
        params={"exchange": "SSE", "list_status": "L"}, obtained_at="2026-10-25T09:00:00+08:00")
    row = adapter.standardize("ts_a_basic", raw, {})["securities"].iloc[0]
    assert row.security_type == "b_share"
    assert row.code == "900999.SH"


@pytest.mark.parametrize("start,end", [("2026-01-05", "2026-01-05"), ("2026-10-01", "2026-10-25")])
def test_directory_observation_cannot_claim_historical_coverage(tmp_path, start, end):
    ds, _ = store(tmp_path, product="ts_a_basic")
    manifest, files = _provider_import_items(tmp_path, "ts_a_basic", "stock_basic", [response("stock_basic")])
    doc = json.loads(manifest.read_text())
    item = doc["items"][0]
    item["table"] = "securities"
    item["query"]["params"] = {"exchange": "SSE", "list_status": "L"}
    item["request"].update(codes=[], start=start, end=end)
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="observ|histor|directory"):
        ds.import_files(manifest, files)
    assert not [p for p in (ds.root / "snapshots").iterdir() if not p.name.startswith(".tmp")]


@pytest.mark.parametrize("day", ["20251231", "20260107"])
def test_daily_response_outside_requested_period_is_rejected_before_publish(tmp_path, day):
    class OutsidePeriod(SourceClient):
        def query(self, api, params, fields):
            self.calls.append((api, params, fields))
            return response(api, day=day)

    ds, adapter = store(tmp_path, client=OutsidePeriod())
    with pytest.raises(provider_module().IncompleteResponse, match="outside requested period"):
        ds.prepare(request())
    assert len(adapter.client.calls) == 1
    assert not list((ds.root / "snapshots").glob("prep-*/manifest.json"))


def test_suspend_market_day_keeps_intraday_event_and_reuses_snapshot(tmp_path):
    from dataclasses import replace

    from hetu_data.updates import plan_update

    product = "ts_suspend_day"

    class MarketDay:
        calls = []

        def query(self, api, params, fields):
            self.calls.append((api, params, fields))
            return json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
                        "items": [
                            ["SYNTH.SH", "20260105", None, "S"],
                            ["SYNTH.SZ", "20260105", "09:30-10:00", "S"],
                            ["OTHER.SZ", "20260105", None, "S"],
                        ],
                    },
                }
            ).encode()

    client = MarketDay()
    ds, _ = store(tmp_path, client, product=product, codes=["SYNTH.SH", "SYNTH.SZ"], markets=["SH", "SZ"],
                  acquire_cross_section=True)
    req = replace(request(product, end="2026-01-05"), codes=())
    plan = plan_update(req, ds, {"products": {product: {"max_days": 1}}})
    assert plan["counts"]["network_attempts"] == 1
    ref = ds.prepare(req)
    assert ds.prepare(req) == ref
    assert set(ds.read_table(ref, "security_events").code) == {"SYNTH.SH", "SYNTH.SZ"}
    assert client.calls == [("suspend_d", {"trade_date": "20260105"}, "ts_code,trade_date,suspend_timing,suspend_type")]
    frame = ds.read_range(ref, "security_events", codes=("SYNTH.SZ",), start=req.start, end=req.end,
                          columns=("code", "date", "event_type", "suspend_timing"))
    assert frame.to_dict("records") == [
        {"code": "SYNTH.SZ", "date": "2026-01-05", "event_type": "S", "suspend_timing": "09:30-10:00"}
    ]
    with pytest.raises(ValueError, match="single day"):
        ds.prepare(replace(req, end=date(2026, 1, 6)))


@pytest.mark.parametrize("condition", ["permitted", "read_only", "tampered"])
def test_market_day_expanded_scope_replays_saved_original_without_another_request(tmp_path, condition):
    from dataclasses import replace

    from hetu_data.updates import execute_update, plan_update

    product = "ts_suspend_day"

    class MarketDay:
        calls = 0

        def query(self, api, params, fields):
            self.calls += 1
            assert self.calls == 1, "saved raw is enough"
            return json.dumps({"code": 0, "data": {"fields": fields.split(","), "items": [
                ["SYNTH.SH", "20260105", None, "S"], ["SYNTH.SZ", "20260105", "09:30-10:00", "S"],
            ]}}).encode()

    client = MarketDay()
    ds, adapter = store(tmp_path, client, product=product, acquire_cross_section=True)
    req = replace(request(product, end="2026-01-05"), codes=())
    original = ds.prepare(req)
    manifest_path = ds.root / "snapshots" / original.snapshot_id / "manifest.json"
    before = manifest_path.read_bytes()
    assert ds.read_table(original, "security_events").code.tolist() == ["SYNTH.SH"]

    new_grant = grant(product, markets=["SH", "SZ"], codes=["SYNTH.SH", "SYNTH.SZ", "ABSENT.SZ"],
                      allowed_operations=["read"] if condition == "read_only" else ["read", "import"])
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": [new_grant]}))
    if condition == "tampered":
        manifest = json.loads(before)
        raw = next(item for item in manifest["items"][0]["raw_files"] if item.get("api"))
        source = manifest_path.parent / raw["path"]
        source.write_bytes(source.read_bytes() + b" ")
    reopened = DataStore(ds.root, providers={"tushare": adapter})
    contract = {"products": {product: {"max_days": 1}}, "limits": {"approved_scope_ref": "synthetic-expansion",
                "provider_fetch": 0, "network_attempts": 0}}
    plan = plan_update(req, reopened, contract)
    assert len(plan["segments"]) == 1
    assert plan["counts"]["network_attempts"] == plan["counts"]["provider_fetch"] == 0
    result = execute_update(
        plan, reopened, tmp_path / "expansion.jsonl", cancelled=lambda: False, progress=lambda _: None,
    )
    assert result["actual_usage"]["network_attempts"] == result["actual_usage"]["provider_fetch"] == 0
    if condition != "permitted":
        expected = "AccessDenied" if condition == "read_only" else "SnapshotTamperedError"
        assert result["failures"][0]["error_type"] == expected
        assert not result["completed"] and client.calls == 1
        return
    assert not result["failures"] and not result["pending"]
    new_ref = reopened.prepare(req)
    assert new_ref.snapshot_id != original.snapshot_id
    assert set(reopened.read_table(new_ref, "security_events").code) == {"SYNTH.SH", "SYNTH.SZ"}
    assert reopened.load_manifest(new_ref)["revises"] == original.snapshot_id
    assert manifest_path.read_bytes() == before and client.calls == 1
    assert reopened.plan(req)["segments"] == []


def test_market_scope_expansion_uses_newer_narrow_revision_before_older_wide_snapshot(tmp_path):
    from dataclasses import replace

    product = "ts_adj_factor_day"
    req = replace(request(product, end="2026-01-05"), codes=())
    query = {"api": "adj_factor", "params": {"trade_date": "20260105"}}
    codes = ["SYNTH.SH", "SECOND.SH"]

    def facts(factor):
        return json.dumps({"code": 0, "data": {"fields": ["ts_code", "trade_date", "adj_factor"],
                           "items": [[code, "20260105", factor] for code in codes]}})

    ds, _ = store(tmp_path, product=product, codes=codes, allowed_operations=["read", "import"])
    entitlements = ds.root / "data-entitlements.json"
    wide = entitlements.read_text()
    raw = tmp_path / "factors.json"
    doc = tmp_path / "import.json"
    doc.write_text(json.dumps({"mode": "paid", "items": [{
        "product_id": product, "table": "adjustment_factors", "format": "provider_json",
        "files": [raw.name], "request": data_request_to_dict(req), "query": query,
        "source": {"name": "tushare", "fee_class": "paid", "permission_basis": "synthetic",
                   "obtained_at": "2026-01-05T17:00:00+08:00"},
    }]}))
    raw.write_text(facts(1))
    old = ds.import_files(doc, (raw,))
    entitlements.write_text(json.dumps({"grants": [grant(product, allowed_operations=["read", "import"])]}))
    raw.write_text(facts(2))
    latest = ds.import_files(doc, (raw,))
    entitlements.write_text(wide)
    current = ds.prepare(req)
    assert ds.read_table(old, "adjustment_factors").adj_factor.tolist() == [1, 1]
    assert ds.read_table(latest, "adjustment_factors").adj_factor.tolist() == [2]
    assert ds.read_table(current, "adjustment_factors").adj_factor.tolist() == [2, 2]
    assert ds.load_manifest(current)["revises"] == latest.snapshot_id


def test_market_factor_day_bounds_rows_and_checks_date(tmp_path):
    from dataclasses import replace

    class MarketDay:
        def query(self, api, params, fields):
            assert (api, params, fields) == ("adj_factor", {"trade_date": "20260105"}, "ts_code,trade_date,adj_factor")
            return json.dumps(dict(code=0, data=dict(
                fields=["ts_code", "trade_date", "adj_factor"],
                items=[["SYNTH.SH", "20260105", 2.0], ["EXTRA.SH", "20260105", 3.0]],
            ))).encode()

    product = "ts_adj_factor_day"
    ds, adapter = store(tmp_path, MarketDay(), product=product, acquire_cross_section=True)
    req = replace(request(product, end="2026-01-05"), codes=())
    ref = ds.prepare(req)
    assert ds.prepare(req) == ref
    frame = ds.read_table(ref, "adjustment_factors")
    assert frame[["code", "adj_factor"]].to_dict("records") == [{"code": "SYNTH.SH", "adj_factor": 2.0}]
    invalid = adapter.client.query("adj_factor", {"trade_date": "20260105"}, "ts_code,trade_date,adj_factor")
    with pytest.raises(ValueError, match="outside requested day"):
        adapter.decode_response(invalid, api="adj_factor", params={"trade_date": "20260106"},
                                obtained_at="2026-10-25T09:00:00+08:00")


def test_sz_calendar_validates_exchange_and_keeps_legacy_default(tmp_path):
    from dataclasses import replace

    class ShenzhenCalendar:
        def query(self, api, params, fields):
            assert api == "trade_cal" and params["exchange"] == "SZSE"
            return b'{"code":0,"data":{"fields":["exchange","cal_date","is_open"],"items":[["SZSE","20260105",1]]}}'

    product = "ts_trade_calendar_sz"
    ds, adapter = store(tmp_path, ShenzhenCalendar(), product=product, markets=["SZ"])
    ref = ds.prepare(replace(request(product, end="2026-01-05"), codes=()))
    assert ds.read_table(ref, "calendar").is_trading_day.tolist() == [True]
    invalid = adapter.client.query("trade_cal", {"exchange": "SZSE"}, "").replace(b"SZSE", b"SSE")
    with pytest.raises(ValueError, match="another exchange"):
        adapter.decode_response(invalid, api="trade_cal",
                                params={"exchange": "SZSE", "start_date": "20260105", "end_date": "20260105"},
                                obtained_at="2026-10-25T09:00:00+08:00")


@pytest.mark.parametrize(("day", "kind", "error"), [
    ("20260104", "S", "outside requested period"),
    ("20260105", "X", "unrecognized suspension event type"),
])
def test_per_code_suspension_response_enforces_date_and_type(day, kind, error):
    adapter = provider_module().TushareProvider()
    packet = dict(code=0, data=dict(fields=["ts_code", "trade_date", "suspend_timing", "suspend_type"],
                                  items=[["SYNTH.SH", day, None, kind]]))
    params = {"ts_code": "SYNTH.SH", "start_date": "20260105", "end_date": "20260106"}
    with pytest.raises(ValueError, match=error):
        adapter.decode_response(json.dumps(packet).encode(), api="suspend_d", params=params,
                                obtained_at="2026-10-25T09:00:00+08:00")


def test_market_st_list_preserves_positive_observation_without_filling_status(tmp_path):
    from dataclasses import replace

    class RiskList:
        def query(self, api, params, fields):
            assert (api, params, fields) == ("stock_st", {"trade_date": "20260105"},
                                             "ts_code,name,trade_date,type,type_name")
            return json.dumps(dict(code=0, data=dict(
                fields=fields.split(","),
                items=[["SYNTH.SH", "ST合成", "20260105", "ST", "风险警示板"]],
            ))).encode()

    product = "ts_st_day"
    ds, _ = store(tmp_path, RiskList(), product=product, acquire_cross_section=True)
    req = replace(request(product, end="2026-01-05"), codes=())
    ref = ds.prepare(req)
    assert ds.read_table(ref, "security_events")[["code", "date", "event_type"]].to_dict("records") == [
        {"code": "SYNTH.SH", "date": "2026-01-05", "event_type": "ST"}
    ]
    assert "security_status" not in {entry["table"] for entry in ds.load_manifest(ref)["table_files"]}


def test_bse_code_mapping_preserves_listing_observation_without_effective_date(tmp_path):
    from dataclasses import replace

    class Mapping:
        def query(self, api, params, fields):
            assert (api, params, fields) == ("bse_mapping", {}, "name,o_code,n_code,list_date")
            return json.dumps(dict(code=0, data=dict(
                fields=fields.split(","),
                items=[["合成", "83SYNTH.BJ", "92SYNTH.BJ", "20260105"]],
            ))).encode()

    product = "ts_bse_mapping"
    ds, _ = store(
        tmp_path, Mapping(), product=product, codes=["92SYNTH.BJ"], markets=["BJ"], acquire_cross_section=True
    )
    req = replace(request(product, end="2026-01-05"), codes=())
    ref = ds.prepare(req)
    frame = ds.read_table(ref, "security_observations")
    assert frame[["code", "old_code", "date"]].to_dict("records") == [
        {"code": "92SYNTH.BJ", "old_code": "83SYNTH.BJ", "date": "2026-01-05"}
    ]
    assert "effective_from" not in frame


def test_object_grant_cannot_fetch_whole_market_raw_response(tmp_path):
    from dataclasses import replace

    product = "ts_adj_factor_day"
    ds, adapter = store(tmp_path, product=product)
    req = replace(request(product, end="2026-01-05"), codes=())
    with pytest.raises(AccessDenied, match="whole-market raw response"):
        ds.prepare(req)
    assert not adapter.client.calls


def test_wrong_market_grant_rejects_sz_calendar_without_call(tmp_path):
    ds, adapter = store(tmp_path, product="ts_trade_calendar_sz", markets=["SH"])
    with pytest.raises(AccessDenied, match="product market outside current grant"):
        ds.prepare(request("ts_trade_calendar_sz"))
    assert not adapter.client.calls


def test_market_day_import_requires_matching_query_day(tmp_path):
    from dataclasses import replace

    from hetu_data.models import data_request_to_dict

    product = "ts_suspend_day"
    ds, adapter = store(tmp_path, product=product, acquire_cross_section=True)
    req = replace(request(product, end="2026-01-05"), codes=())
    raw = tmp_path / "response.json"
    raw.write_text(json.dumps({"code": 0, "data": {
        "fields": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
        "items": [["SYNTH.SH", "20260104", None, "S"]],
    }}))
    doc = tmp_path / "import.json"
    doc.write_text(json.dumps({"mode": "paid", "items": [{
        "product_id": product, "table": "security_events", "format": "provider_json",
        "files": [raw.name], "request": data_request_to_dict(req),
        "query": {"api": "suspend_d", "params": {}},
        "source": {"name": "synthetic", "fee_class": "paid", "permission_basis": "synthetic",
                   "obtained_at": "2026-01-05T09:00:00+08:00"},
    }]}))
    with pytest.raises(ValueError, match="requires trade_date"):
        ds.import_files(doc, (raw,))
    assert not list((ds.root / "snapshots").glob("imp-*"))
    assert not adapter.client.calls


def test_sz_calendar_import_requires_complete_query_bounds(tmp_path):
    from dataclasses import replace

    from hetu_data.models import data_request_to_dict

    product = "ts_trade_calendar_sz"
    ds, adapter = store(tmp_path, product=product, markets=["SZ"])
    req = replace(request(product, end="2026-01-05"), codes=())
    raw = tmp_path / "response.json"
    raw.write_text('{"code":0,"data":{"fields":["exchange","cal_date","is_open"],"items":[["SSE","20260105",1]]}}')
    doc = tmp_path / "import.json"
    doc.write_text(json.dumps({"mode": "paid", "items": [{
        "product_id": product, "table": "calendar", "format": "provider_json",
        "files": [raw.name], "request": data_request_to_dict(req),
        "query": {"api": "trade_cal", "params": {"exchange": "SZSE"}},
        "source": {"name": "synthetic", "fee_class": "paid", "permission_basis": "synthetic",
                   "obtained_at": "2026-01-05T09:00:00+08:00"},
    }]}))
    with pytest.raises(ValueError, match="requires both queried dates"):
        ds.import_files(doc, (raw,))
    assert not list((ds.root / "snapshots").glob("imp-*"))
    assert not adapter.client.calls


def test_planning_reuses_manifest_parse_but_detects_replaced_proof(tmp_path, monkeypatch):
    from pathlib import Path

    from hetu_data.store import SnapshotTamperedError

    ds, adapter = store(tmp_path)
    req = request(end="2026-01-05")
    ref = ds.prepare(req)
    path = ref.root / "snapshots" / ref.snapshot_id / "manifest.json"
    reads = []
    original_read_text = Path.read_text

    def observe(file, *args, **kwargs):
        if file == path:
            reads.append(file)
        return original_read_text(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observe)
    assert not ds.plan(req)["segments"]
    assert not ds.plan(req)["segments"]
    assert reads == [path]

    manifest = json.loads(original_read_text(path))
    bar = next(entry for entry in manifest["table_files"] if entry["table"] == "daily_bars")
    bar["sha256"] = "0" * 64
    replacement = path.with_name("replacement.json")
    replacement.write_text(json.dumps(manifest))
    replacement.replace(path)
    with pytest.raises(SnapshotTamperedError, match="locked asset"):
        ds.plan(req)
    assert len(adapter.client.calls) == 1
