"""G2只读规划、预算断点与可选年度布局；所有证券均为合成。"""

import hashlib
import json
from dataclasses import replace
from datetime import date

from hetu_quant.models import data_request_from_dict, data_request_to_dict
from test_data_sources import grant, request, store


def updates_module():
    from hetu_quant import data_updates

    return data_updates


def contracts(**limits):
    return {
        "products": {"ts_a_daily": {"max_days": 6000, "upstream_per_fetch": 1, "sdk_calls": 0}},
        "limits": {
            "provider_fetch": 3,
            "upstream_product_requests": 3,
            "network_attempts": 3,
            "approved_scope_ref": "synthetic-G2-test",
            **limits,
        },
    }


def test_plan_update_is_offline_and_subtracts_coverage(tmp_path):
    ds, adapter = store(tmp_path)
    ds.prepare(request(end="2026-01-05"))
    before = len(adapter.client.calls)
    plan = updates_module().plan_update(request(), ds, contracts())
    assert len(adapter.client.calls) == before
    assert len(plan["segments"]) == 1
    assert plan["segments"][0]["start"] == "2026-01-06"
    assert plan["counts"]["upstream_product_requests"] == 1


def test_resume_only_fetches_pending_segments(tmp_path):
    ds, adapter = store(tmp_path)
    plan = updates_module().plan_update(request(), ds, contracts())
    ledger = tmp_path / "usage.jsonl"
    result = updates_module().execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    count = len(adapter.client.calls)
    again = updates_module().execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert count == len(adapter.client.calls) == 1
    assert result["completed"] == again["completed"]
    assert again["actual_usage"]["network_attempts"] == 1


def test_budget_counts_pages_retries_and_quota_separately(tmp_path):
    ds, adapter = store(tmp_path)

    # 一次fetch真实调用两个受控上游产品；第二次必须在发出之前被预算拒绝。
    def two_calls(product, codes, start, end):
        adapter.before_request(
            {"upstream_product_requests": 1, "network_attempts": 1, "sdk_calls": 0, "auth_calls": 0, "retries": 0}
        )
        adapter.on_usage({"rows": 1, "quota_bytes": None, "failed": False})
        adapter.before_request(
            {"upstream_product_requests": 1, "network_attempts": 1, "sdk_calls": 0, "auth_calls": 0, "retries": 1}
        )
        raise AssertionError("budget guard should stop before second upstream")

    adapter.fetch = two_calls
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts(network_attempts=1, upstream_product_requests=1))
    result = module.execute_update(plan, ds, tmp_path / "usage.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert result["failures"]
    assert result["actual_usage"]["provider_fetch"] == 1
    assert result["actual_usage"]["network_attempts"] == 1
    assert result["actual_usage"]["quota_bytes"] is None
    assert ds.plan(request())["segments"]  # 失败不得登记完整窗口。


def test_cancelled_update_does_not_call_provider(tmp_path):
    ds, adapter = store(tmp_path)
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts())
    result = module.execute_update(plan, ds, tmp_path / "usage.jsonl", cancelled=lambda: True, progress=lambda _: None)
    assert result["pending"]
    assert adapter.client.calls == []


def test_refresh_preserves_old_version(tmp_path):
    from source_cases import SourceClient

    ds, adapter = store(tmp_path, SourceClient("daily_basic"), product="ts_a_market_cap")
    first = ds.prepare(request("ts_a_market_cap", end="2026-01-05"))
    path = ds.root / "snapshots" / first.snapshot_id
    before = {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()
    }
    original_query = adapter.client.query

    def revised(api, params, fields):
        payload = json.loads(original_query(api, params, fields))
        payload["data"]["items"][0][4] = 1100
        return json.dumps(payload).encode()

    adapter.client.query = revised
    req = request("ts_a_market_cap", end="2026-01-05", refresh_from=date(2026, 1, 5))
    second = ds.prepare(req)
    caps = ds.read_table(second, "market_cap")
    assert sorted(caps.market_cap_cny.tolist()) == [10000000, 11000000]
    assert second.snapshot_id != first.snapshot_id
    assert all(hashlib.sha256((path / p).read_bytes()).hexdigest() == h for p, h in before.items())
    assert data_request_from_dict(data_request_to_dict(req)).refresh_from == date(2026, 1, 5)
    old = data_request_to_dict(request())
    old.pop("refresh_from", None)
    assert data_request_from_dict(old).refresh_from is None


def test_year_layout_preserves_object_coverage_and_old_read(tmp_path):
    from source_cases import response

    ds, _ = store(tmp_path, allowed_operations=["read", "import"])
    grants = [grant(allowed_operations=["read", "import"], codes=["SYNTH.SH", "NEW.SH"])]
    (ds.root / "data-entitlements.json").write_text(json.dumps({"grants": grants}))
    items, files = [], []
    for i, (code, start, end) in enumerate(
        [("SYNTH.SH", "2025-12-31", "2026-01-05"), ("NEW.SH", "2026-01-02", "2026-01-05")]
    ):
        payload = json.loads(response(code=code, day=start.replace("-", "")))
        payload["data"]["items"].append([code, end.replace("-", ""), 10, 11, 9, 10, 9, 12.34, 56.78])
        raw = tmp_path / f"raw{i}.json"
        raw.write_text(json.dumps(payload))
        files.append(raw)
        req = replace(request(start=start, end=end), codes=(code,))
        items.append(
            {
                "product_id": "ts_a_daily",
                "table": "daily_bars",
                "files": [raw.name],
                "format": "provider_json",
                "request": data_request_to_dict(req),
                "query": {
                    "api": "daily",
                    "params": {"ts_code": code, "start_date": start.replace("-", ""), "end_date": end.replace("-", "")},
                },
                "source": {
                    "name": "tushare",
                    "fee_class": "paid",
                    "permission_basis": "synthetic licensed fixture",
                    "obtained_at": "2026-10-25T09:00:00+08:00",
                },
            }
        )
    doc = tmp_path / "import.json"
    doc.write_text(json.dumps({"mode": "paid", "items": items}))
    yearly = ds.import_files(doc, tuple(files))
    m = ds.load_manifest(yearly)
    bars = ds.read_table(yearly, "daily_bars")
    actual = list((ds.root / "snapshots" / yearly.snapshot_id / "tables/daily_bars").glob("*.parquet"))
    assert len(actual) == 2
    cov = m["object_coverage"]
    assert min(c["start"] for c in cov if c["code"] == "NEW.SH") == "2026-01-02"
    assert {p["compression"] for p in m["table_files"] if p["table"] == "daily_bars"} == {"zstd"}
    # 同事实用旧布局导入，读取结果相同，不改旧路径或旧快照。
    ds.config["products"]["ts_a_daily"].pop("storage_layout")
    legacy = ds.import_files(doc, tuple(files))
    import pandas as pd

    pd.testing.assert_frame_equal(
        bars.sort_values(["code", "date"]).reset_index(drop=True),
        ds.read_table(legacy, "daily_bars").sort_values(["code", "date"]).reset_index(drop=True),
    )


def test_year_copy_refresh_reopen_keeps_evidence(tmp_path):
    from hetu_quant.data import DataStore

    ds, adapter = store(tmp_path, codes=["SYNTH.SH", "OTHER.SH"])
    first = ds.prepare(request(end="2026-01-05"))
    parent = ds.root / "snapshots" / first.snapshot_id
    before = {
        str(p.relative_to(parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in parent.rglob("*") if p.is_file()
    }
    ds.prepare(request())
    both = replace(request(), codes=("SYNTH.SH", "OTHER.SH"))
    last = ds.prepare(both)
    assert set(ds.read_table(last, "daily_bars").code) == {"SYNTH.SH", "OTHER.SH"}
    assert len(list((ds.root / "snapshots" / last.snapshot_id / "tables/daily_bars").glob("*.parquet"))) == 1
    reopened = DataStore(ds.root, providers={"tushare": adapter})
    calls = len(adapter.client.calls)
    again = reopened.prepare(both)
    assert again.snapshot_id == last.snapshot_id
    assert len(adapter.client.calls) == calls
    assert len(reopened.read_table(last, "knowledge_evidence")) >= 3
    assert all(hashlib.sha256((parent / p).read_bytes()).hexdigest() == h for p, h in before.items())


def test_prepare_does_not_expand_unrelated_yearly_object_coverage(tmp_path, monkeypatch):
    from hetu_data import store as store_module

    ds, adapter = store(tmp_path, codes=["SYNTH.SH", "OTHER.SH"])
    unrelated = replace(request(start="2025-01-06", end="2025-01-06"), codes=("OTHER.SH",))
    ds.prepare(unrelated)
    month_keys = store_module._month_keys

    def only_relevant_months(start, end):
        assert start.year != 2025, "planning one code must not expand another code's yearly coverage"
        return month_keys(start, end)

    monkeypatch.setattr(store_module, "_month_keys", only_relevant_months)
    requested = request(end="2026-01-05")
    first = ds.prepare(requested)
    assert set(ds.read_table(first, "daily_bars").code) == {"SYNTH.SH"}
    calls = len(adapter.client.calls)
    assert ds.prepare(requested) == first
    assert len(adapter.client.calls) == calls


def test_failed_resume_counts_retry_once_and_never_repeats_success(tmp_path):
    from source_cases import SourceClient

    ds, adapter = store(tmp_path, SourceClient(fail=True))
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts(retries=1))
    ledger = tmp_path / "usage.jsonl"
    first = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert first["failures"]
    adapter.client.fail = False
    second = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert second["actual_usage"]["retries"] == 1
    assert second["actual_usage"]["network_attempts"] == 2
    assert second["completed"]
    module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert len(adapter.client.calls) == 2


def test_explicit_resend_preserves_limits_and_cannot_send_twice(tmp_path):
    from source_cases import SourceClient

    ds, adapter = store(tmp_path, SourceClient(fail=True))
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts(retries=0))
    ledger = tmp_path / "usage.jsonl"

    def run(selected):
        return module.execute_update(selected, ds, ledger, cancelled=lambda: False, progress=lambda _: None)

    run(plan)
    failure = next(row for row in map(json.loads, ledger.read_text().splitlines()) if row["type"] == "failed")
    assert run(plan)["actual_usage"]["network_attempts"] == 1
    resend = {**plan, "authorized_resends": [{"segment": failure["segment"], "failed_at": failure["at"]}]}
    result = run(resend)
    assert result["actual_usage"]["authorized_resends"] == 1
    assert result["actual_usage"]["retries"] == 0
    assert result["actual_usage"]["network_attempts"] == 2
    adapter.client.fail = False
    assert run(resend)["actual_usage"]["network_attempts"] == 2
    assert len(adapter.client.calls) == 2


def test_failed_response_bytes_remain_without_coverage(tmp_path):
    from source_cases import response

    ds, adapter = store(tmp_path)
    payload = json.loads(response())
    payload["data"]["has_more"] = True
    body = json.dumps(payload).encode()
    adapter.client.query = lambda *_: body
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts())
    ledger = tmp_path / "usage.jsonl"
    result = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert result["failures"]
    assert ds.plan(request())["segments"]
    raws = list((ledger.parent / "raw-attempts").glob("*.json"))
    assert len(raws) == 1
    assert raws[0].read_bytes() == body


def maintenance_case(tmp_path):
    """新增事实仅表达15日、两生命周期及原始响应；答案留各测试。"""
    import pandas as pd

    from source_cases import SourceClient, response

    ds, adapter = store(tmp_path, codes=["SYNTH.SH", "NEW.SH"])
    (ds.root / "data-entitlements.json").write_text(
        json.dumps(
            {"grants": [grant(pid, codes=["SYNTH.SH", "NEW.SH"]) for pid in ["ts_a_daily", "ts_action_versions"]]}
        )
    )
    history = pd.DataFrame(
        [
            dict(
                code="SYNTH.SH",
                market="SH",
                security_type="stock",
                valid_from="2000-01-01",
                valid_to="2026-01-10",
                lifecycle_status="known",
                source_ref="synthetic-exit-fact",
            ),
            dict(
                code="NEW.SH",
                market="SH",
                security_type="stock",
                valid_from="2026-01-08",
                valid_to="",
                lifecycle_status="known",
                source_ref="synthetic-IPO-fact",
            ),
        ]
    )
    history.to_csv(ds.root / "maintenance-history.csv", index=False)
    calendar = pd.DataFrame(
        [
            dict(date=d.date().isoformat(), is_trading_day=d.weekday() < 5)
            for d in pd.date_range("2026-01-01", "2026-01-15")
        ]
    )
    cal = tmp_path / "calendar.csv"
    calendar.to_csv(cal, index=False)
    manifest = tmp_path / "calendar-import.json"
    manifest.write_text(
        json.dumps(
            dict(
                mode="free",
                items=[
                    dict(
                        product_id="cn_trade_calendar",
                        table="calendar",
                        files=[cal.name],
                        source=dict(
                            name="synthetic-calendar",
                            fee_class="free",
                            permission_basis="synthetic facts",
                            obtained_at="2026-01-01T00:00:00+08:00",
                        ),
                    )
                ],
            )
        )
    )
    ds.import_files(manifest, (cal,))
    adapter.client = SourceClient()
    calls = adapter.client.calls

    def query(api, params, fields):
        calls.append((api, params, fields))
        if api == "daily":
            payload = json.loads(response(code=params["ts_code"]))
            days = pd.bdate_range(params["start_date"], params["end_date"])
            payload["data"]["items"] = [
                json.loads(response(code=params["ts_code"], day=d.strftime("%Y%m%d")))["data"]["items"][0]
                for d in days
                if d.strftime("%Y%m%d") != "20260102"
            ]
            return json.dumps(payload).encode()
        payload = json.loads(response("dividend", code=params["ts_code"], day="20260112"))
        payload["data"]["items"][0][2] = "20260110"  # 周六公告，不改为交易日。
        payload["data"]["items"][0][10] = "20260110"
        return json.dumps(payload).encode()

    adapter.client.query = query
    spec = contracts(provider_fetch=20, upstream_product_requests=20, network_attempts=20, retries=0)
    spec["products"]["ts_a_daily"].update(time_basis="trading_days", table="daily_bars", revision_lookback_days=0)
    spec["products"]["ts_action_versions"] = dict(
        max_days=6000,
        upstream_per_fetch=1,
        sdk_calls=0,
        time_basis="natural_days",
        table="action_versions",
        revision_lookback_days=7,
        historical_revisions=False,
        unavailable_revisions=[
            dict(code="SYNTH.SH", published_at="2025-12-28", reason="source_did_not_retain_revision")
        ],
    )
    spec["maintenance"] = dict(
        start="2026-01-01",
        history_ref=str(ds.root / "maintenance-history.csv"),
        target_scope=dict(complete=False, source_ref="synthetic-independent-history"),
        calendar_markets=["SH"],
    )
    return ds, adapter, spec


def test_fifteen_calendar_days_include_weekend_events_and_exits(tmp_path):
    ds, adapter, spec = maintenance_case(tmp_path)
    ds.prepare(request(start="2026-01-01", end="2026-01-05"))
    calls = len(adapter.client.calls)
    plan = updates_module().maintenance_plan(
        ds, date(2026, 1, 15), ("ts_a_daily", "ts_action_versions"), mode="paid", contracts=spec
    )
    domains = {d["product_id"]: d for d in plan["domains"]}
    daily, events = domains["ts_a_daily"], domains["ts_action_versions"]
    assert plan["start"] == "2026-01-01" and plan["target"] == "2026-01-15"
    assert daily["last_success"] == "2026-01-05"  # 取得时间在10月，不能当数据截止。
    assert dict(code="SYNTH.SH", start="2026-01-02", end="2026-01-02") in daily["missing"]
    assert any(r["code"] == "NEW.SH" and r["start"] == "2026-01-08" for r in daily["missing"])
    assert all(r["end"] <= "2026-01-09" for r in daily["missing"] if r["code"] == "SYNTH.SH")
    assert events["time_basis"] == "natural_days"
    assert events["target_codes"] == ["SYNTH.SH", "NEW.SH"]
    assert any(
        r["code"] == "SYNTH.SH" and r["start"] == "2026-01-09" and r["end"] == "2026-01-15" for r in events["refresh"]
    )
    assert events["unrecoverable"] == [
        dict(code="SYNTH.SH", published_at="2025-12-28", reason="source_did_not_retain_revision")
    ]
    assert len(adapter.client.calls) == calls  # 真实规划无传输。


def test_recovery_reopens_only_missing_work_and_reports_incremental_usage(tmp_path, monkeypatch):
    from hetu_quant.data import DataStore

    ds, adapter = store(tmp_path)
    module = updates_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    spec = contracts(retries=1)
    spec["products"]["ts_a_daily"]["max_days"] = 1
    plan = module.plan_update(request(), ds, spec)
    original = adapter.client.query
    failed = False

    def query(api, params, fields):
        nonlocal failed
        if params["start_date"] == "20260106" and not failed:
            failed = True
            adapter.client.calls.append((api, params, fields))
            raise ConnectionError("synthetic interrupted last segment")
        return original(api, params, fields)

    adapter.client.query = query
    ledger = tmp_path / "usage.jsonl"
    first = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert first["failures"] and len(first["snapshot_ids"]) == 1
    reopened = DataStore(ds.root, providers={"tushare": adapter})
    second = module.execute_update(plan, reopened, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert second["incremental_usage"]["provider_fetch"] == 1
    assert second["actual_usage"]["provider_fetch"] == 3
    before = len(adapter.client.calls)
    duplicate = module.execute_update(plan, reopened, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert duplicate["incremental_usage"]["provider_fetch"] == 0
    assert duplicate["actual_usage"]["provider_fetch"] == 3 and len(adapter.client.calls) == before
    assert reopened.read_table(
        __import__("hetu_quant.models", fromlist=["SnapshotRef"]).SnapshotRef(second["snapshot_ids"][-1], ds.root),
        "daily_bars",
    ).date.tolist() == ["2026-01-05", "2026-01-06"]


def test_late_revision_is_not_fabricated_and_unknown_calendar_is_scoped(tmp_path):
    ds, _, spec = maintenance_case(tmp_path)
    spec["products"]["ts_a_daily"]["time_basis"] = "unknown"
    plan = updates_module().maintenance_plan(
        ds, date(2026, 1, 15), ("ts_a_daily", "ts_action_versions"), mode="paid", contracts=spec
    )
    domains = {d["product_id"]: d for d in plan["domains"]}
    assert domains["ts_a_daily"]["unknown"] and not any(s["product_id"] == "ts_a_daily" for s in plan["segments"])
    assert domains["ts_action_versions"]["unrecoverable"]
    assert any(s["product_id"] == "ts_action_versions" for s in plan["segments"])


def test_uncertain_attempt_is_not_resent(tmp_path):
    ds, adapter = store(tmp_path)
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts())
    segment = {k: plan["segments"][0][k] for k in ("product_id", "codes", "start", "end", "refresh")}
    ledger = tmp_path / "usage.jsonl"
    ledger.write_text(json.dumps(dict(type="started", segment=segment)) + "\n")
    result = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert "uncertain" in result["pending"][0]["reason"] and adapter.client.calls == []


def test_entitlement_expiry_preserves_allowed_old_reads(tmp_path):
    import pytest

    from hetu_quant.data_archive import query_archive

    ds, adapter = store(tmp_path)
    ref = ds.prepare(request(end="2026-01-05"))
    path = ds.root / "data-entitlements.json"
    path.write_text(json.dumps({"grants": [grant(acquire_until="2000-01-01", retained_use=True)]}))
    before = len(adapter.client.calls)
    with pytest.raises(PermissionError):
        ds.prepare(request())
    result = query_archive(ds, ref, codes=("SYNTH.SH",), start=date(2026, 1, 5), end=date(2026, 1, 5))
    assert result["bars"].close_raw.tolist() == [10] and len(adapter.client.calls) == before


def test_worker_budget_stop_persists_usage_and_old_snapshot(tmp_path, monkeypatch):
    from hetu_quant import worker
    from hetu_quant.experiments import ExperimentStore
    from hetu_quant.jobs import JobService

    ds, adapter = store(tmp_path)
    module = updates_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    spec = contracts(provider_fetch=1, network_attempts=1, upstream_product_requests=1)
    spec["products"]["ts_a_daily"]["max_days"] = 1
    plan = module.plan_update(request(), ds, spec)
    exp = ExperimentStore(ds.root)
    did = exp.save_definition("maintenance", plan)
    jobs = JobService(exp)
    job = jobs.submit(did, "synthetic-budget-stop")
    worker.run_job(job.job_id, exp, ds)
    final = jobs.get(job.job_id)
    assert final.state == "failed"
    report = json.loads((final.output / "update.json").read_text())
    assert report["actual_usage"]["provider_fetch"] == report["incremental_usage"]["provider_fetch"] == 1
    assert report["actual_usage"]["network_attempts"] == 1 and len(adapter.client.calls) == 1
    assert report["snapshot_ids"] and report["pending"]


def test_maintenance_successful_empty_is_unknown_content_not_repeated(tmp_path):
    from source_cases import response

    ds, adapter, spec = maintenance_case(tmp_path)
    adapter.client.query = lambda *_: response(empty=True)
    ds.prepare(request(start="2026-01-01", end="2026-01-05"))
    plan = updates_module().maintenance_plan(ds, date(2026, 1, 15), ("ts_a_daily",), mode="paid", contracts=spec)
    domain = plan["domains"][0]
    assert domain["successful_empty"] and any(
        r["reason"] == "successful_empty_not_content_coverage" for r in domain["unknown"]
    )
    assert all(s["start"] > "2026-01-05" for s in plan["segments"] if s["codes"] == ["SYNTH.SH"])


def test_maintenance_invalid_rows_are_not_covered(tmp_path):
    from source_cases import response

    ds, adapter, spec = maintenance_case(tmp_path)
    payload = json.loads(response())
    payload["data"]["items"][0][5] = 0
    adapter.client.query = lambda *_: json.dumps(payload).encode()
    ds.prepare(request(end="2026-01-05"))
    plan = updates_module().maintenance_plan(ds, date(2026, 1, 15), ("ts_a_daily",), mode="paid", contracts=spec)
    domain = plan["domains"][0]
    assert not domain["covered"] and any(r["reason"] == "invalid_or_unknown_rows" for r in domain["unknown"])


def test_unmetered_transport_and_unknown_quota_never_send(tmp_path):
    ds, adapter = store(tmp_path)
    module = updates_module()
    plan = module.plan_update(request(), ds, contracts(quota_bytes=100))
    result = module.execute_update(plan, ds, tmp_path / "usage.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert result["failures"] and adapter.client.calls == []


def test_weekend_revision_consumed_by_real_provider_and_storage(tmp_path, monkeypatch):
    from hetu_quant.models import SnapshotRef

    ds, adapter, spec = maintenance_case(tmp_path)
    module = updates_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    plan = module.maintenance_plan(ds, date(2026, 1, 15), ("ts_action_versions",), mode="paid", contracts=spec)
    result = module.execute_update(plan, ds, tmp_path / "usage.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert not result["failures"] and result["incremental_usage"]["provider_fetch"] == 2
    frames = [ds.read_table(SnapshotRef(sid, ds.root), "action_versions") for sid in result["snapshot_ids"]]
    import pandas as pd

    facts = pd.concat(frames)
    assert set(facts.code) == {"SYNTH.SH", "NEW.SH"} and set(facts.ann_date) == {"20260110"}
    assert facts.published_at.isna().all()  # 公告字段/取得时间仍不伪装已证PIT。
    assert len(adapter.client.calls) == 2


def test_calendar_maintenance_reuses_rows_without_historical_pool(tmp_path):
    ds, adapter, spec = maintenance_case(tmp_path)
    path = ds.root / "data-entitlements.json"
    grants = json.loads(path.read_text())["grants"] + [grant("ts_trade_calendar", codes=[])]
    path.write_text(json.dumps(dict(grants=grants)))
    spec["products"] = {
        "ts_trade_calendar": dict(max_days=6000, upstream_per_fetch=1, time_basis="natural_days", table="calendar")
    }
    spec["maintenance"].pop("history_ref")
    plan = updates_module().maintenance_plan(ds, date(2026, 1, 15), ("ts_trade_calendar",), mode="paid", contracts=spec)
    assert not plan["segments"] and not plan["domains"][0]["missing"]
    assert plan["domains"][0]["last_success"] == "2026-01-15" and adapter.client.calls == []


def test_existing_approval_limits_cannot_be_raised_by_new_plan(tmp_path):
    import pytest

    ds, adapter = store(tmp_path)
    module = updates_module()
    plan = module.plan_update(request(end="2026-01-05"), ds, contracts(provider_fetch=1))
    ledger = tmp_path / "usage.jsonl"
    module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    increased = {**plan, "limits": {**plan["limits"], "provider_fetch": 20}}
    with pytest.raises(PermissionError):
        module.execute_update(increased, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert len(adapter.client.calls) == 1


def test_multi_request_fetch_cancel_resumes_only_unfinished_upstream(tmp_path, monkeypatch):
    from hetu_quant.models import SnapshotRef
    from source_cases import SourceClient, response

    ds, adapter = store(tmp_path, SourceClient("stock_basic"), product="ts_a_basic")
    module = updates_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    cancelled = [False]
    calls = []

    def query(api, params, fields):
        calls.append(params["list_status"])
        payload = json.loads(response("stock_basic", code=params["ts_code"]))
        if params["list_status"] == "L":
            cancelled[0] = True
        else:
            payload["data"]["items"] = []
        return json.dumps(payload).encode()

    adapter.client.query = query
    spec = dict(
        products={"ts_a_basic": dict(max_days=6000, upstream_per_fetch=3)},
        limits=dict(
            provider_fetch=2,
            upstream_product_requests=3,
            network_attempts=3,
            retries=1,
            approved_scope_ref="synthetic-multi-request-cancel",
        ),
    )
    plan = module.plan_update(request("ts_a_basic"), ds, spec)
    ledger = tmp_path / "usage.jsonl"
    first = module.execute_update(plan, ds, ledger, cancelled=lambda: cancelled[0], progress=lambda _: None)
    assert first["failures"][0]["error_type"] == "InterruptedError" and calls == ["L"]
    original_response = next(
        json.loads(line)["response"]
        for line in ledger.read_text().splitlines()
        if json.loads(line)["type"] == "response"
    )
    cancelled[0] = False
    second = module.execute_update(plan, ds, ledger, cancelled=lambda: cancelled[0], progress=lambda _: None)
    assert not second["pending"] and not second["failures"]
    assert calls == ["L", "D", "P"]  # 首个成功原件不再发送，不消耗多余上游额度。
    assert second["incremental_usage"]["network_attempts"] == 2
    assert second["actual_usage"]["network_attempts"] == 3
    assert second["actual_usage"]["provider_fetch"] == 2 and second["actual_usage"]["retries"] == 1
    securities = ds.read_table(SnapshotRef(second["snapshot_ids"][-1], ds.root), "securities")
    assert securities.code.tolist() == ["SYNTH.SH"]
    assert securities.observed_at.iloc[0] == original_response["obtained_at"]
    again = module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert again["incremental_usage"]["provider_fetch"] == 0 and calls == ["L", "D", "P"]


def test_daily_maintenance_preserves_full_revision_window_and_exited_objects(tmp_path):
    ds, _, spec = maintenance_case(tmp_path)
    spec["maintenance"]["start"] = "2026-01-15"
    plan = updates_module().maintenance_plan(
        ds, date(2026, 1, 15), ("ts_action_versions",), mode="paid", contracts=spec
    )
    domain = plan["domains"][0]
    assert domain["target_codes"] == ["SYNTH.SH", "NEW.SH"]
    assert domain["refresh"] == [
        dict(code="SYNTH.SH", start="2026-01-09", end="2026-01-15"),
        dict(code="NEW.SH", start="2026-01-09", end="2026-01-15"),
    ]
    assert plan["request"]["start"] == "2026-01-09"
    assert plan["start"] == "2026-01-15"  # 日更目标与来源回看分开，不改原研究/档案。


def test_bounded_calendar_refresh_fetches_and_consumes_revision(tmp_path, monkeypatch):
    from pathlib import Path

    import pandas as pd
    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import submit_update_plan
    from hetu_data.library import publish_library, read_library_range
    from hetu_data.models import LibraryRef, SnapshotRef
    from hetu_data.worker import run_maintenance_job

    ds, adapter = store(tmp_path, product="ts_trade_calendar", codes=[])
    spec = contracts()
    spec["maintenance"] = dict(start="2026-01-09")
    grants_path = ds.root / "data-entitlements.json"
    grants_path.write_text(json.dumps(dict(grants=[grant("ts_trade_calendar", codes=[])])))
    calls = []

    def query(api, params, fields):
        assert api == "trade_cal"
        calls.append(params)
        dates = pd.date_range(params["start_date"], params["end_date"])
        return json.dumps(
            dict(
                code=0,
                data=dict(
                    fields=["exchange", "cal_date", "is_open", "pretrade_date"],
                    items=[
                        ["SSE", d.strftime("%Y%m%d"), int(d.weekday() < 5 and not (len(calls) > 1 and d.day == 15)), ""]
                        for d in dates
                    ],
                ),
            )
        ).encode()

    adapter.client.query = query
    original = ds.prepare(replace(request("ts_trade_calendar", start="2026-01-09", end="2026-01-15"), codes=()))
    original_library = publish_library(ds, asset_snapshots=(
        dict(root=str(ds.root), snapshot_id=original.snapshot_id, tables=["calendar"]),
    ))
    spec["library_ref"] = dict(library_id=original_library.library_id, root=str(original_library.root))
    parent_path = ds.root / "snapshots" / original.snapshot_id / "manifest.json"
    parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    spec["maintenance"]["start"] = "2026-01-09"
    spec["products"] = {
        "ts_trade_calendar": dict(
            max_days=6000, sdk_calls=0, time_basis="natural_days", table="calendar", revision_lookback_days=7
        )
    }
    module = updates_module()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    plan = module.maintenance_plan(ds, date(2026, 1, 15), ("ts_trade_calendar",), mode="paid", contracts=spec)
    assert plan["domains"][0]["refresh"] == [dict(code="", start="2026-01-09", end="2026-01-15")]
    tasks = DataTaskStore(ds.root)
    job = submit_update_plan(tasks, plan, "synthetic-calendar-refresh")
    final = run_maintenance_job(job.job_id, tasks, ds)
    result = json.loads((final.output / "update.json").read_text())
    assert final.state == "succeeded"
    assert not result["pending"] and not result["failures"]
    assert result["incremental_usage"]["provider_fetch"] == 1 and len(calls) == 2
    fresh = SnapshotRef(result["snapshot_ids"][-1], ds.root)
    assert fresh.snapshot_id != original.snapshot_id
    assert ds.read_table(fresh, "calendar").set_index("date").loc["2026-01-15", "is_trading_day"] == False  # noqa: E712
    assert ds.read_table(original, "calendar").set_index("date").loc["2026-01-15", "is_trading_day"] == True  # noqa: E712
    assert hashlib.sha256(parent_path.read_bytes()).hexdigest() == parent_hash

    def library_calendar(ref):
        return read_library_range(
            ds, ref, "calendar", codes=(), start=date(2026, 1, 15), end=date(2026, 1, 15),
            columns=("is_trading_day",),
        ).is_trading_day.tolist()

    assert library_calendar(original_library) == [True]
    assert library_calendar(LibraryRef(**result["library_ref"])) == [False]
    after = module.maintenance_plan(ds, date(2026, 1, 15), ("ts_trade_calendar",), mode="paid", contracts=spec)
    assert not after["domains"][0]["missing"]  # 维护消费新版本，不能把被明确修订的旧值再算冲突。
    again = module.execute_update(
        plan, ds, Path(result["approved_usage_ledger"]), cancelled=lambda: False, progress=lambda _: None
    )
    assert again["incremental_usage"]["provider_fetch"] == 0 and len(calls) == 2


def test_maintenance_reuses_explicit_other_root_snapshot_without_migration(tmp_path):
    from hetu_quant.data_archive import scan_local_coverage

    ds, adapter, spec = maintenance_case(tmp_path)
    other_dir = tmp_path / "registered-owner"
    other_dir.mkdir()
    other, _ = store(other_dir)
    ref = other.prepare(request(end="2026-01-05"))
    original_root_files = sorted(str(p.relative_to(ds.root)) for p in ds.root.rglob("*") if p.is_file())
    reference = dict(root=str(other.root), snapshot_id=ref.snapshot_id, tables=["daily_bars"])
    seed = ds._published_manifests()[0][0]
    from hetu_quant.models import SnapshotRef

    inventory = scan_local_coverage(ds, SnapshotRef(seed, ds.root), asset_snapshots=[reference])
    assert inventory.masks["SYNTH.SH"]["bars"].bit_count() == 1
    spec["maintenance"].update(start="2026-01-05", asset_snapshots=[reference])
    plan = updates_module().maintenance_plan(ds, date(2026, 1, 5), ("ts_a_daily",), mode="paid", contracts=spec)
    assert not plan["segments"]
    assert plan["domains"][0]["covered"] == [dict(code="SYNTH.SH", start="2026-01-05", end="2026-01-05")]
    assert plan["counts"]["provider_fetch"] == 0 and adapter.client.calls == []
    assert original_root_files == sorted(str(p.relative_to(ds.root)) for p in ds.root.rglob("*") if p.is_file())


def test_external_coverage_reuses_only_source_licensed_objects(tmp_path):
    ds, adapter, spec = maintenance_case(tmp_path)
    path = tmp_path / "subset-source"
    path.mkdir()
    other, _ = store(path)  # 原件读取许可只有SYNTH，不包括NEW。
    ref = other.prepare(request(end="2026-01-05"))
    spec["maintenance"].update(
        start="2026-01-05",
        asset_snapshots=[dict(root=str(other.root), snapshot_id=ref.snapshot_id, tables=["daily_bars"])],
    )
    plan = updates_module().maintenance_plan(ds, date(2026, 1, 8), ("ts_a_daily",), mode="paid", contracts=spec)
    assert plan["domains"][0]["covered"] == [dict(code="SYNTH.SH", start="2026-01-05", end="2026-01-05")]
    assert [(s["codes"], s["start"], s["end"]) for s in plan["segments"]] == [
        (["SYNTH.SH"], "2026-01-06", "2026-01-08"),
        (["NEW.SH"], "2026-01-08", "2026-01-08"),
    ]
    assert adapter.client.calls == []


def test_batched_update_stops_after_first_failed_segment(tmp_path):
    from source_cases import SourceClient

    ds, adapter = store(tmp_path, SourceClient(fail=True))
    module = updates_module()
    first = dict(product_id="ts_a_daily", codes=["SYNTH.SH"], start="2026-01-05", end="2026-01-05", refresh=False)
    second = dict(first, start="2026-01-06", end="2026-01-06")
    plan = module.plan_update(request(), ds, contracts(stop_on_failure=True), audited_segments=[first, second])
    result = module.execute_update(plan, ds, tmp_path / "usage.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert len(adapter.client.calls) == 1
    assert len(result["failures"]) == 1
    assert result["pending"] == [first, dict(second, reason="earlier segment failed")]


def test_daily_quota_resume_preserves_batch_usage(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import pytest
    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import JobService, submit_update_plan
    from hetu_data.library import list_libraries, publish_library, query_library
    from hetu_data.models import LibraryRef
    from hetu_data.worker import run_maintenance_job

    ds, adapter = store(tmp_path)
    from hetu_data import updates as module
    zone = ZoneInfo("Asia/Shanghai")
    baseline = ds.prepare(request(end="2026-01-05"))
    history = publish_library(ds, asset_snapshots=(
        dict(root=str(ds.root), snapshot_id=baseline.snapshot_id, tables=["daily_bars"]),
    ))
    adapter.client.calls.clear()
    clock = [datetime(2026, 1, 7, 23, 50, tzinfo=zone)]
    monkeypatch.setattr(module, "_quota_now", lambda tz: clock[0].astimezone(tz), raising=False)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    spec = contracts(
        provider_fetch=2,
        network_attempts=2,
        upstream_product_requests=2,
        daily_limits={"network_attempts": 1},
        quota_scope_ref="synthetic-source-account",
        quota_timezone="Asia/Shanghai",
    )
    spec["products"]["ts_a_daily"]["max_days"] = 1
    spec["library_ref"] = dict(library_id=history.library_id, root=str(history.root))
    plan = module.plan_update(request(start="2026-01-06", end="2026-01-07"), ds, spec)
    tasks = DataTaskStore(ds.root)

    def execute(frozen_plan, submission):
        job = submit_update_plan(tasks, frozen_plan, submission)
        final = run_maintenance_job(job.job_id, tasks, ds)
        return final, json.loads((final.output / "update.json").read_text())

    final, first = execute(plan, "quota-first")
    assert len(adapter.client.calls) == 1
    assert final.state == "failed" and first["stop_reason"] == "daily_limit"
    assert not first["failures"] and first["pending"][0]["start"] == "2026-01-07"
    assert first["resume_after"] == "2026-01-08T00:00:00+08:00"
    assert first["daily_usage"]["usage"]["network_attempts"] == 1
    assert first["actual_usage"]["provider_fetch"] == 1
    assert first["last_success_at"] == "2026-01-07T23:50:00+08:00"

    def dates(ref):
        return query_library(
            ds, LibraryRef(**ref), codes=("SYNTH.SH",), start=date(2026, 1, 5), end=date(2026, 1, 8),
        )["bars"].date.tolist()

    assert dates(first["library_ref"]) == ["2026-01-05", "2026-01-06"]
    assert len(list_libraries(ds)) == 2
    reopened = JobService(tasks).retry(final.job_id, "quota-reopened")
    repeated = run_maintenance_job(reopened.job_id, tasks, ds)
    same_day = json.loads((repeated.output / "update.json").read_text())
    assert same_day["incremental_usage"]["network_attempts"] == 0
    assert same_day["actual_usage"]["network_attempts"] == 1
    assert same_day["library_ref"] == first["library_ref"] and len(list_libraries(ds)) == 2

    other_spec = {**spec, "limits": {**spec["limits"], "approved_scope_ref": "synthetic-other-approval"}}
    other_plan = module.plan_update(request(start="2026-01-08", end="2026-01-08"), ds, other_spec)
    _, other = execute(other_plan, "quota-other-approval")
    assert len(adapter.client.calls) == 1 and other["stop_reason"] == "daily_limit"
    assert other["daily_usage"]["usage"]["network_attempts"] == 1
    assert other["actual_usage"]["network_attempts"] == 0

    clock[0] = clock[0].replace(day=8, hour=1)
    active = submit_update_plan(tasks, plan, "quota-next-day")
    source_query = adapter.client.query

    def cancel_after_response(*args):
        payload = source_query(*args)
        JobService(tasks).cancel(active.job_id)
        return payload

    monkeypatch.setattr(adapter.client, "query", cancel_after_response)
    final = run_maintenance_job(active.job_id, tasks, ds)
    second = json.loads((final.output / "update.json").read_text())
    assert final.state == "cancelled" and second["stop_reason"] == "cancelled" and not second["pending"]
    assert len(adapter.client.calls) == second["actual_usage"]["network_attempts"] == 2
    assert second["daily_usage"]["usage"]["network_attempts"] == 1
    assert second["incremental_usage"]["network_attempts"] == 1
    assert second["snapshot_ids"][0] == first["snapshot_ids"][0]
    assert dates(second["library_ref"]) == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert dates(first["library_ref"]) == ["2026-01-05", "2026-01-06"]
    assert len(list_libraries(ds)) == 3
    recovered, repeated = execute(plan, "quota-after-cancel")
    assert recovered.state == "succeeded" and repeated["library_ref"] == second["library_ref"]
    assert repeated["incremental_usage"]["network_attempts"] == 0 and len(list_libraries(ds)) == 3
    monkeypatch.setattr(adapter.client, "query", source_query)

    clock[0] = clock[0].replace(day=9)
    third_plan = module.plan_update(request(start="2026-01-08", end="2026-01-08"), ds, spec)
    _, third = execute(third_plan, "quota-batch-exhausted")
    assert len(adapter.client.calls) == 2 and third["stop_reason"] == "batch_limit"
    assert third["resume_after"] is None and third["actual_usage"]["network_attempts"] == 2
    with pytest.raises(PermissionError, match="limits changed"):
        module.execute_update(
            {**plan, "limits": {**plan["limits"], "network_attempts": 20}},
            ds,
            ds.root / first["approved_usage_ledger"],
            cancelled=lambda: False,
            progress=lambda _: None,
        )


def test_daily_quota_inside_fetch_resumes_remaining_calls(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hetu_data.models import SnapshotRef
    from hetu_data.worker import approved_usage_ledger

    from source_cases import SourceClient, response

    ds, adapter = store(tmp_path, SourceClient("stock_basic"), product="ts_a_basic")
    from hetu_data import updates as module
    clock = [datetime(2026, 1, 7, 23, 50, tzinfo=ZoneInfo("Asia/Shanghai"))]
    monkeypatch.setattr(module, "_quota_now", lambda tz: clock[0].astimezone(tz), raising=False)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    calls = []

    def query(api, params, fields):
        status = params["list_status"]
        calls.append(status)
        return response(api, code=params["ts_code"], empty=status != "L")

    adapter.client.query = query
    spec = dict(
        products={"ts_a_basic": dict(max_days=6000, upstream_per_fetch=3)},
        limits=dict(
            provider_fetch=3,
            network_attempts=3,
            retries=0,
            approved_scope_ref="synthetic-daily-inside-fetch",
            daily_limits={"network_attempts": 1},
            quota_scope_ref="synthetic-source-account",
            quota_timezone="Asia/Shanghai",
        ),
    )
    plan = module.plan_update(request("ts_a_basic"), ds, spec)
    ledger = approved_usage_ledger(ds.root, spec["limits"]["approved_scope_ref"])

    def execute():
        return module.execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)

    first = execute()
    assert calls == ["L"]
    assert first["stop_reason"] == "daily_limit" and not first["failures"] and first["pending"]
    saved = next(e["response"] for e in map(json.loads, ledger.read_text().splitlines()) if e["type"] == "response")
    assert execute()["incremental_usage"]["provider_fetch"] == 0
    clock[0] = clock[0].replace(day=8, hour=1)
    assert execute()["stop_reason"] == "daily_limit" and calls == ["L", "D"]
    clock[0] = clock[0].replace(day=9)
    third = execute()
    assert calls == ["L", "D", "P"] and not third["pending"] and not third["failures"]
    assert third["actual_usage"]["network_attempts"] == third["actual_usage"]["provider_fetch"] == 3
    assert third["actual_usage"]["retries"] == 0
    securities = ds.read_table(SnapshotRef(third["snapshot_ids"][-1], ds.root), "securities")
    assert securities.code.tolist() == ["SYNTH.SH"]
    assert securities.observed_at.iloc[0] == saved["obtained_at"]



def test_daily_quota_worker_can_retry_failed_library_publication_without_fetch(tmp_path, monkeypatch):
    from hetu_data import library
    from hetu_data.catalog import DataTaskStore
    from hetu_data.jobs import JobService, submit_update_plan
    from hetu_data.models import LibraryRef
    from hetu_data.worker import run_maintenance_job

    ds, adapter = store(tmp_path)
    baseline = ds.prepare(request(end="2026-01-05"))
    published = library.publish_library(ds, asset_snapshots=(
        dict(root=str(ds.root), snapshot_id=baseline.snapshot_id, tables=["daily_bars"]),
    ))
    ref = dict(library_id=published.library_id, root=str(published.root))
    spec = {**contracts(), "library_ref": ref}
    plan = updates_module().plan_update(request(start="2026-01-06", end="2026-01-06"), ds, spec)
    tasks = DataTaskStore(ds.root)
    first = submit_update_plan(tasks, plan, "publish-first")
    publish = library.publish_library

    def fail(*args, **kwargs):
        raise OSError("synthetic unavailable publication destination")

    monkeypatch.setattr(library, "publish_library", fail)
    first = run_maintenance_job(first.job_id, tasks, ds)
    interrupted = json.loads((first.output / "update.json").read_text())
    assert first.state == "failed" and interrupted["library_ref"] == ref
    assert len(library.list_libraries(ds)) == 1 and len(adapter.client.calls) == 2

    monkeypatch.setattr(library, "publish_library", publish)
    second = JobService(tasks).retry(first.job_id, "publish-retry")
    second = run_maintenance_job(second.job_id, tasks, ds)
    recovered = json.loads((second.output / "update.json").read_text())
    assert second.state == "succeeded" and recovered["snapshot_ids"] == interrupted["snapshot_ids"]
    assert recovered["incremental_usage"]["network_attempts"] == 0 and len(adapter.client.calls) == 2
    assert len(library.list_libraries(ds)) == 2
    query = library.query_library(
        ds, LibraryRef(**recovered["library_ref"]), codes=("SYNTH.SH",),
        start=date(2026, 1, 5), end=date(2026, 1, 6),
    )
    assert query["bars"].date.tolist() == ["2026-01-05", "2026-01-06"]
