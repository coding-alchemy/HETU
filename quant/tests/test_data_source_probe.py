"""合成HTTP事实仅替换网络；验证真实探查的授权、原件和预算行为。"""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).parents[1] / "probes/probe_data_source.py"
    spec = importlib.util.spec_from_file_location("data_source_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def material(tmp_path, *, attempts=3, retries=0, count=3):
    """一份合成授权及单码查询；期望不写入执行材料。"""
    root = tmp_path / "selection"
    path = root / "synthetic"
    path.mkdir(parents=True)
    scope = {"objects": [{"code": "SYNTH.SH"}], "industry_case": {"code": "SYNTH.SI"},
             "budget": {"total_product_call_attempts_max": attempts, "max_retry_attempts": retries,
                        "global_rate_max_per_minute": 30}}
    requests = [{"id": f"S{i}", "api": "daily", "params": {"ts_code": "SYNTH.SH", "trade_date": f"2026010{i}"},
                 "fields": "ts_code,trade_date,close", "single_call_row_limit": 6000} for i in range(1, count + 1)]
    (path / "sample-scope.json").write_text(json.dumps(scope))
    (path / "request-list.json").write_text(json.dumps({"requests": requests}))
    auth = {"g1_approved": True, "scope_ref": "synthetic/sample-scope.json",
            "requests_ref": "synthetic/request-list.json",
            "approved_limits": {"total_http_product_attempts_max": attempts, "shared_retry_reserve": retries}}
    (root / "g1-authorization-20261024.json").write_text(json.dumps(auth))
    return path / "sample-scope.json", tmp_path / "output"


RAW = (b'{"code":0,"msg":null,"data":{"fields":["ts_code","trade_date","close"],'
       b'"items":[["SYNTH.SH","20260101",10],["SYNTH.SH","20260101",11]]}}')


def network(monkeypatch, probe, responses):
    calls = []

    class Transport:
        def send(self, api, params, fields, token):
            calls.append((api, params, fields))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(probe, "HTTPTransport", Transport, raising=False)
    monkeypatch.setattr(probe, "load_token", lambda: "SYNTHETIC_SECRET", raising=False)
    monkeypatch.setattr(probe, "wait_for_rate", lambda *_: None, raising=False)
    return calls


def test_probe_plan_does_not_call_client(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path)
    calls = network(monkeypatch, probe, [])
    result = probe.probe_source(scope, output)
    assert result["status"] == "plan_only"
    assert result["planned_requests"] == 3
    assert calls == []
    assert not output.exists()


def test_probe_budget_counts_failed_attempt(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, attempts=2)
    calls = network(monkeypatch, probe, [(200, RAW), TimeoutError()])
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 2
    assert result["status"] == "budget_exhausted"
    assert len(calls) == 2
    assert len(list((output / "raw").glob("*.json"))) == 1


def test_probe_keeps_response_and_meter_units(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, count=1)
    network(monkeypatch, probe, [(200, RAW)])
    result = probe.probe_source(scope, output, execute=True)
    assert next((output / "raw").glob("*.json")).read_bytes() == RAW
    assert result["rows"] == 2
    assert result["sdk_calls"] == result["provider_fetch"] == result["auth_calls"] == 0
    assert result["supplier_quota_bytes"] is None


def test_reopen_reuses_finished_requests(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, count=1)
    calls = network(monkeypatch, probe, [(200, RAW)])
    probe.probe_source(scope, output, execute=True)
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 1
    assert len(calls) == 1


def test_auth_failure_stops_all_without_retry(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, retries=1)
    calls = network(monkeypatch, probe, [(200, b'{"code":-2001,"msg":"invalid token","data":null}')])
    result = probe.probe_source(scope, output, execute=True)
    assert result["status"] == "authentication_blocked"
    assert result["attempts"] == 1
    assert result["retries"] == 0
    assert len(calls) == 1


def test_retry_is_explicit_and_counted(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, count=1, retries=1)
    calls = network(monkeypatch, probe, [(503, b"temporary service error"), (200, RAW)])
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 2
    assert result["retries"] == 1
    assert result["rows"] == 2
    assert len(calls) == 2


def test_permission_failure_stops_affected_endpoint(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path)
    calls = network(monkeypatch, probe, [(200, '{"code":-2002,"msg":"权限不足","data":null}'.encode())])
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 1
    assert result["skipped_requests"] == ["S2", "S3"]
    assert len(calls) == 1


def test_row_limit_does_not_trigger_implicit_page(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path)
    requests = json.loads(scope.with_name("request-list.json").read_text())
    requests["requests"][0]["single_call_row_limit"] = 2
    scope.with_name("request-list.json").write_text(json.dumps(requests))
    calls = network(monkeypatch, probe, [(200, RAW)])
    result = probe.probe_source(scope, output, execute=True)
    assert result["results"][0]["status"] == "row_limit_reached"
    assert len(calls) == 1


def test_unapproved_scope_never_reaches_network(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path)
    auth = scope.parent.parent / "g1-authorization-20261024.json"
    data = json.loads(auth.read_text())
    data["g1_approved"] = False
    auth.write_text(json.dumps(data))
    calls = network(monkeypatch, probe, [])
    with pytest.raises(ValueError, match="approved"):
        probe.probe_source(scope, output, execute=True)
    assert calls == []


def test_secrets_are_removed_from_echoed_response(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, count=1)
    network(monkeypatch, probe, [(200, b'{"code":-2001,"msg":"SYNTHETIC_SECRET","data":null}')])
    probe.probe_source(scope, output, execute=True)
    assert all(b"SYNTHETIC_SECRET" not in p.read_bytes() for p in output.rglob("*") if p.is_file())


def test_resume_uncertain_attempt_does_not_repeat_request(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path)
    output.mkdir()
    (output / "sample-ledger.jsonl").write_text('{"event":"started","attempt":1,"id":"S1","retry":false}\n')
    calls = network(monkeypatch, probe, [])
    result = probe.probe_source(scope, output, execute=True)
    assert result["status"] == "interrupted_attempt_unknown"
    assert result["attempts"] == 1
    assert calls == []


def test_resume_rejects_changed_raw_without_reacquisition(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, count=1)
    calls = network(monkeypatch, probe, [(200, RAW)])
    probe.probe_source(scope, output, execute=True)
    next((output / "raw").glob("*.json")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw"):
        probe.probe_source(scope, output, execute=True)
    assert len(calls) == 1


def test_auth_stop_reason_survives_last_budget_slot(tmp_path, monkeypatch, probe):
    scope, output = material(tmp_path, attempts=1)
    network(monkeypatch, probe, [(200, b'{"code":-2001,"msg":"invalid token","data":null}')])
    result = probe.probe_source(scope, output, execute=True)
    assert result["status"] == "authentication_blocked"


def directory_material(tmp_path):
    scope, output = material(tmp_path, attempts=9, count=9)
    scope.write_text(json.dumps({"kind": "stock_basic_directory", "objects": [],
        "industry_case": {"code": "SYNTH.SI"}, "budget": {
        "total_product_call_attempts_max": 9, "max_retry_attempts": 0,
        "global_rate_max_per_minute": 30}}))
    fields = "ts_code,symbol,name,fullname,exchange,market,list_status,list_date,delist_date"
    requests = [{"id": f"DIR{i}", "api": "stock_basic", "params": {"exchange": exchange, "list_status": status},
                 "fields": fields, "single_call_row_limit": 6000}
                for i, (exchange, status) in enumerate([
                    ("SSE", "L"), ("SSE", "D"), ("SSE", "P"),
                    ("SZSE", "L"), ("SZSE", "D"), ("SZSE", "P"),
                    ("BSE", "L"), ("BSE", "D"), ("BSE", "P")], 1)]
    scope.with_name("request-list.json").write_text(json.dumps({"requests": requests}))
    auth = {"directory_approved": True, "scope_ref": scope.name, "requests_ref": "request-list.json",
            "approved_limits": {"total_http_product_attempts_max": 9, "shared_retry_reserve": 0}}
    scope.with_name("authorization.json").write_text(json.dumps(auth))
    bodies = []
    for request in requests:
        exchange, status = request["params"].values()
        items = [] if status != "L" else [["SYNTH." + {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}[exchange],
            "SYNTH", "synthetic", "synthetic full name", exchange, "synthetic market", status, "20000101", None]]
        bodies.append((200, json.dumps({"code": 0, "data": {"fields": fields.split(","),
            "items": items, "has_more": False, "count": 0}}).encode()))
    return scope, output, bodies


def test_directory_needs_its_own_approval_not_g1(tmp_path, monkeypatch, probe):
    scope, output, bodies = directory_material(tmp_path)
    auth = json.loads(scope.with_name("authorization.json").read_text())
    auth["directory_approved"] = False
    scope.with_name("authorization.json").write_text(json.dumps(auth))
    calls = network(monkeypatch, probe, bodies)
    with pytest.raises(ValueError, match="approved"):
        probe.probe_source(scope, output, execute=True)
    assert calls == []


def test_directory_runs_nine_explicit_queries_then_reopens_offline(tmp_path, monkeypatch, probe):
    scope, output, bodies = directory_material(tmp_path)
    calls = network(monkeypatch, probe, bodies)
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 9
    assert result["rows"] == 3
    assert result["retries"] == result["provider_fetch"] == 0
    assert len(list((output / "raw").glob("*.json"))) == 9
    assert {tuple(c[1].values()) for c in calls} == {
        ("SSE", "L"), ("SSE", "D"), ("SSE", "P"), ("SZSE", "L"), ("SZSE", "D"),
        ("SZSE", "P"), ("BSE", "L"), ("BSE", "D"), ("BSE", "P")}
    assert probe.probe_source(scope, output, execute=True)["attempts"] == 9
    assert len(calls) == 9


@pytest.mark.parametrize("mutation", ["api", "extra_parameter", "duplicate_slice"])
def test_directory_rejects_queries_outside_approved_contract(tmp_path, monkeypatch, probe, mutation):
    scope, output, bodies = directory_material(tmp_path)
    requests = json.loads(scope.with_name("request-list.json").read_text())
    first = requests["requests"][0]
    if mutation == "api":
        first["api"] = "daily"
    elif mutation == "extra_parameter":
        first["params"]["ts_code"] = "SYNTH.SH"
    else:
        requests["requests"][1]["params"] = dict(first["params"])
    scope.with_name("request-list.json").write_text(json.dumps(requests))
    calls = network(monkeypatch, probe, bodies)
    with pytest.raises(ValueError, match="directory|scope"):
        probe.probe_source(scope, output, execute=True)
    assert calls == []


@pytest.mark.parametrize("mutation", ["exchange", "list_status", "has_more"])
def test_directory_rejects_unproven_slice_without_extra_requests(tmp_path, monkeypatch, probe, mutation):
    scope, output, bodies = directory_material(tmp_path)
    body = json.loads(bodies[0][1])
    if mutation == "has_more":
        body["data"]["has_more"] = True
    else:
        body["data"]["items"][0][body["data"]["fields"].index(mutation)] = "SZSE" if mutation == "exchange" else "D"
    bodies[0] = (200, json.dumps(body).encode())
    calls = network(monkeypatch, probe, bodies)
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 1
    assert result["results"][0]["status"] not in {"success", "success_empty"}
    assert len(calls) == 1


def test_directory_no_retry_even_on_network_failure(tmp_path, monkeypatch, probe):
    scope, output, bodies = directory_material(tmp_path)
    calls = network(monkeypatch, probe, [TimeoutError(), *bodies[1:]])
    result = probe.probe_source(scope, output, execute=True)
    assert result["attempts"] == 9
    assert result["retries"] == 0
    assert result["results"][0]["status"] == "network_failure"
    assert result["status"] == "limited"
    assert probe.probe_source(scope, output, execute=True)["attempts"] == 9
    assert len(calls) == 9
