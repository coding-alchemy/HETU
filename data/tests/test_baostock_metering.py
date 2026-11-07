"""Controlled BaoStock status uses the real SDK parser without any live socket."""

import base64
import hashlib
import json
import zlib
from datetime import date

import pytest

from hetu_data.providers.baostock import DAILY_FIELDS, BaoStockProvider

START = date(2016, 4, 1)
END = date(2016, 4, 28)
CODE = "600005.SH"
# Synthetic source facts in SDK field order.
ROW = ["2016-04-01", "sh.600005", "", "", "", "", "2", "0", "0", "3", "", "0", "", "1"]


def wire(kind, fields, *, compressed=False, body_newline=False):
    from baostock.data.messageheader import to_message_header

    body = "\x01".join(fields).encode()
    if body_newline:
        body += b"\n"
    payload = zlib.compress(body) if compressed else body
    header = to_message_header(kind, len(payload)).encode()
    trailer = b"\x011234567890\n" if compressed else b""
    return header + payload + trailer + b"<![CDATA[]]>\n"


def status_response(rows, *, body_newline=False, end=END):
    return wire(
        "96",
        ["0", "", "query_history_k_data_plus", "anonymous", "1", "2000", json.dumps({"record": rows}),
         "sh.600005", DAILY_FIELDS, str(START), str(end), "d", "3"],
        compressed=True,
        body_newline=body_newline,
    )


def day_response(rows, *, fields="date,code,tradestatus,isST"):
    return wire(
        "99", ["0", "", "query_daily_history_k_AStock", "anonymous", json.dumps({"record": rows}), fields],
        compressed=True,
    )


class FakeSocket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.packet = b""
        self.closed = False
        self.sent = 0
        self.connected = 0

    def connect(self, address):
        self.connected += 1
        assert self.connected == 1

    def settimeout(self, timeout):
        assert 0 < timeout <= 30

    def sendall(self, data):
        self.sent += 1
        self.packet = next(self.responses)

    def recv(self, limit):
        part, self.packet = self.packet[:limit], self.packet[limit:]
        return part

    def close(self):
        self.closed = True


def provider(monkeypatch, *responses):
    from baostock.data.resultset import ResultData

    fake = FakeSocket([wire("01", ["0", "", "login", "anonymous"]), *responses])
    monkeypatch.setattr("socket.socket", lambda *a, **k: fake)
    monkeypatch.setattr(ResultData, "next", lambda self: pytest.fail("metered status must not page via SDK next()"))
    started, received = [], []
    client = BaoStockProvider()
    client.before_request, client.on_usage = started.append, received.append
    return client, fake, started, received


@pytest.mark.parametrize("body_newline", [False, True])
def test_metered_status_keeps_actual_tcp_bytes_and_reuses_login(monkeypatch, body_newline):
    response = status_response([ROW], body_newline=body_newline)
    client, fake, started, received = provider(monkeypatch, response, status_response([]))
    result = client.fetch("cn_a_status", (CODE,), START, END)
    status = client.standardize("cn_a_status", result, {})["security_status"]
    assert status.to_dict("records") == [
        {"code": CODE, "date": "2016-04-01", "is_st": True, "is_suspended": True, "is_listed": True}
    ]
    proof = result.attrs["provider_responses"][0]
    assert proof["source"] == "baostock"
    assert base64.b64decode(json.loads(proof["body"])["response_base64"]) == response
    assert client.fetch("cn_a_status", (CODE,), START, END).empty
    assert [e["category"] for e in started] == ["connection", "auth", "product", "product"]
    assert sum(e["network_attempts"] for e in started) == 4
    assert sum(e["upstream_product_requests"] for e in started) == 2
    assert sum(e["auth_calls"] for e in started) == 1
    assert [e["rows"] for e in received if e["category"] == "product"] == [1, 0]
    client.close()
    assert fake.closed and fake.sent == 3


@pytest.mark.parametrize(
    "response",
    [b"", status_response([ROW, ROW]), status_response([ROW[:1]]),
     status_response([["2016-04-29", *ROW[1:]]]), status_response([[ROW[0], "sz.000001", *ROW[2:]]])],
)
def test_metered_status_never_publishes_eof_or_malformed_rows(monkeypatch, response):
    client, fake, started, received = provider(monkeypatch, response)
    with pytest.raises((ConnectionError, ValueError)):
        client.fetch("cn_a_status", (CODE,), START, END)
    assert [e["category"] for e in started] == ["connection", "auth", "product"]
    assert received[-1]["category"] == "product" and received[-1]["failed"]
    assert fake.closed


def test_metered_connect_failure_does_not_retry_or_attempt_login(monkeypatch):
    client, fake, started, received = provider(monkeypatch)

    def reject(address):
        raise TimeoutError("synthetic connection timeout")

    monkeypatch.setattr(fake, "connect", reject)
    with pytest.raises(TimeoutError):
        client.fetch("cn_a_status", (CODE,), START, END)
    assert [e["category"] for e in started] == ["connection"]
    assert received[0]["failed"] and fake.closed and fake.sent == 0


def test_metered_long_status_allows_1827_days_but_never_sends_a_wider_window(monkeypatch):
    last = date(2021, 4, 1)
    packet = status_response([ROW, [str(last), *ROW[1:]]], end=last)
    client, fake, started, received = provider(monkeypatch, packet)
    with pytest.raises(ValueError):
        client.fetch("cn_a_status", (CODE,), START, date(2021, 4, 2))
    assert not started and fake.sent == 0
    raw = client.fetch("cn_a_status", (CODE,), START, last)
    status = client.standardize("cn_a_status", raw, {})["security_status"]
    assert status.date.tolist() == [str(START), str(last)]
    assert [event["category"] for event in started] == ["connection", "auth", "product"]
    assert received[-1]["params"]["end_date"] == str(last)
    client.close()


def test_metered_long_status_rejects_a_response_at_the_sdk_page_limit(monkeypatch):
    last = date(2021, 4, 1)
    packet = status_response([ROW] * 2000, end=last)
    client, fake, _, received = provider(monkeypatch, packet)
    with pytest.raises(ValueError, match="SDK row limit"):
        client.fetch("cn_a_status", (CODE,), START, last)
    assert received[-1]["failed"] and "raw_response" in received[-1] and fake.closed


def test_explicit_vendor_rejection_is_not_a_retryable_connection_failure(monkeypatch):
    denial = wire("96", ["100099", "synthetic source rejection"], compressed=True)
    client, fake, _, received = provider(monkeypatch, denial)
    with pytest.raises(PermissionError, match="100099"):
        client.fetch("cn_a_status", (CODE,), START, END)
    assert received[-1]["failed"] and "raw_response" in received[-1] and fake.closed


def test_saved_status_frame_replays_through_fetch_without_connecting(monkeypatch):
    client, fake, started, received = provider(monkeypatch)
    monkeypatch.setattr("socket.socket", lambda *a, **kw: pytest.fail("replay must not connect"))
    frame = status_response([ROW])
    body = json.dumps({"response_base64": base64.b64encode(frame).decode()})
    proof = {
        "api": "query_history_k_data_plus",
        "params": {"code": "sh.600005", "fields": DAILY_FIELDS, "start_date": str(START), "end_date": str(END),
                   "frequency": "d", "adjustflag": "3"},
        "body": body, "obtained_at": "2026-11-07T08:00:00+08:00", "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    client.resume_responses = [proof]
    raw = client.fetch("cn_a_status", (CODE,), START, END)
    assert client.standardize("cn_a_status", raw, {})["security_status"].code.tolist() == [CODE]
    assert raw.attrs["provider_responses"] == [{**proof, "source": "baostock"}]
    assert not started and not received and fake.sent == 0
    proof["body"] += " "
    with pytest.raises(ValueError, match="hash"):
        client.fetch("cn_a_status", (CODE,), START, END)


def test_metered_day_uses_one_sdk_query_and_reports_only_observed_status(monkeypatch):
    packet = day_response([[str(START), "sz.000001", "1", "0"], [str(START), "sh.600005", "0", "1"]])
    client, fake, started, received = provider(monkeypatch, packet, day_response([]))
    raw = client.fetch("cn_a_status_day", (), START, START)
    status = client.standardize("cn_a_status_day", raw, {"tables": ["security_status"]})["security_status"]
    assert status.to_dict("records") == [
        {"date": str(START), "code": "000001.SZ", "is_st": False, "is_suspended": False, "is_listed": True},
        {"date": str(START), "code": CODE, "is_st": True, "is_suspended": True, "is_listed": True},
    ]
    proof, = raw.attrs["provider_responses"]
    assert proof["params"] == {"date": str(START)}
    assert base64.b64decode(json.loads(proof["body"])["response_base64"]) == packet
    assert client.fetch("cn_a_status_day", (), START, START).empty
    assert [e["category"] for e in started] == ["connection", "auth", "product", "product"]
    assert [e["rows"] for e in received if e["category"] == "product"] == [2, 0]
    client.close()
    assert fake.closed and fake.sent == 3


def test_published_baostock_day_reopens_without_a_tushare_scope(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hetu_data import DataRequest, DataStore
    from hetu_data.updates import plan_update

    client, fake, _, _ = provider(monkeypatch, day_response([[str(START), "sh.600005", "0", "1"]]))
    ds = DataStore(tmp_path, providers={"baostock": client})
    product = "cn_a_status_day"
    req = DataRequest((product,), (), START, START, datetime.now(ZoneInfo("Asia/Shanghai")), "daily_archive")
    try:
        ref = ds.prepare(req)
    finally:
        client.close()
    monkeypatch.setattr("socket.socket", lambda *a, **kw: pytest.fail("covered day must not connect"))
    reopened = DataStore(tmp_path, providers={"baostock": BaoStockProvider()})
    assert plan_update(req, reopened, {"products": {product: {"max_days": 1}}})["segments"] == []
    assert reopened.prepare(req) == ref
    assert reopened.read_table(ref, "security_status").code.tolist() == [CODE]
    assert fake.sent == 2


@pytest.mark.parametrize("packet", [
    day_response([[str(START), "sh.600005", "0"]], fields="date,code,isST"),
    day_response([[str(END), "sh.600005", "0", "1"]]),
    day_response([[str(START), "sh.600005", "0", "1"]] * 2),
    day_response([[str(START), "sh.600005", "0", "1"], [str(START), None, "0", "1"]]),
], ids=["missing-status", "wrong-date", "duplicate", "missing-code"])
def test_metered_day_does_not_guess_missing_status_or_accept_wrong_rows(monkeypatch, packet):
    client, fake, started, received = provider(monkeypatch, packet)
    with pytest.raises(ValueError):
        client.fetch("cn_a_status_day", (), START, START)
    assert [e["category"] for e in started] == ["connection", "auth", "product"]
    assert received[-1]["failed"] and "raw_response" in received[-1] and fake.closed


def test_metered_worker_records_success_empty_without_claiming_status(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hetu_data import DataRequest, DataStore, SnapshotRef
    from hetu_data.updates import execute_update, plan_update

    client, fake, _, _ = provider(monkeypatch, status_response([]))
    client.before_request = client.on_usage = None
    ds = DataStore(tmp_path, providers={"baostock": client})
    req = DataRequest(("cn_a_status",), (CODE,), START, END, datetime.now(ZoneInfo("Asia/Shanghai")), "daily_archive")
    plan = plan_update(req, ds, {
        "products": {"cn_a_status": {"max_days": 28, "upstream_per_fetch": 1, "sdk_calls": 1}},
        "limits": {"approved_scope_ref": "synthetic-bao-status", "provider_fetch": 1, "sdk_calls": 2,
                   "network_attempts": 3, "upstream_product_requests": 1, "auth_calls": 1,
                   "requests_per_minute": 500},
    })
    ledger = tmp_path / "ledger.jsonl"
    result = execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert not result["failures"] and not result["pending"]
    assert result["actual_usage"]["network_attempts"] == 3
    assert result["actual_usage"]["auth_calls"] == 1
    ref = SnapshotRef(result["snapshot_ids"][0], ds.root)
    assert ds.read_table(ref, "security_status").empty
    assert ds.load_manifest(ref)["gaps"]["cn_a_status"]
    assert json.loads(ledger.read_text().splitlines()[-1])["query_observation"] == "success_empty"
    repeated = execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert repeated["incremental_usage"]["network_attempts"] == 0


@pytest.mark.parametrize("interrupt", [OSError, KeyboardInterrupt])
def test_worker_reopens_saved_empty_status_after_publication_interrupted(tmp_path, monkeypatch, interrupt):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hetu_data import DataRequest, DataStore, SnapshotRef
    from hetu_data.updates import execute_update, plan_update

    client, fake, _, _ = provider(monkeypatch, status_response([]))
    ds = DataStore(tmp_path, providers={"baostock": client})
    req = DataRequest(("cn_a_status",), (CODE,), START, END, datetime.now(ZoneInfo("Asia/Shanghai")), "daily_archive")
    plan = plan_update(req, ds, {
        "products": {"cn_a_status": {"max_days": 28, "upstream_per_fetch": 1}},
        "limits": {"approved_scope_ref": "synthetic-bao-reopen", "provider_fetch": 1, "network_attempts": 3,
                   "upstream_product_requests": 1, "retries": 0, "requests_per_minute": 500},
    })
    ledger = tmp_path / "ledger.jsonl"

    def stop(*args):
        raise interrupt("synthetic publication interruption")

    with monkeypatch.context() as patch:
        patch.setattr(ds, "_publish", stop)
        if interrupt is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
        else:
            first = execute_update(plan, ds, ledger, cancelled=lambda: False, progress=lambda _: None)
            assert first["failures"][0]["error_type"] == "OSError"
    client.close()
    assert fake.sent == 2
    monkeypatch.setattr("socket.socket", lambda *a, **kw: pytest.fail("reopening must not connect"))
    reopened = DataStore(tmp_path, providers={"baostock": BaoStockProvider()})
    result = execute_update(plan, reopened, ledger, cancelled=lambda: False, progress=lambda _: None)
    assert not result["failures"] and not result["pending"]
    assert result["actual_usage"]["provider_fetch"] == 1
    assert result["actual_usage"]["network_attempts"] == 3
    assert result["incremental_usage"]["provider_fetch"] == result["incremental_usage"]["network_attempts"] == 0
    ref = SnapshotRef(result["snapshot_ids"][0], reopened.root)
    assert reopened.read_table(ref, "security_status").empty
    assert reopened.load_manifest(ref)["gaps"]["cn_a_status"]
    assert json.loads(ledger.read_text().splitlines()[-1])["query_observation"] == "success_empty"
    assert fake.sent == 2
    client.close()


def test_baostock_status_metering_does_not_unlock_other_products(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hetu_data import DataRequest, DataStore
    from hetu_data.updates import execute_update, plan_update

    monkeypatch.setattr("socket.socket", lambda *a, **k: pytest.fail("unmetered product cannot reach a socket"))
    store = DataStore(tmp_path, providers={"baostock": BaoStockProvider()})
    request = DataRequest(("cn_a_actions",), (CODE,), START, END, datetime.now(ZoneInfo("Asia/Shanghai")),
                          "daily_archive")
    plan = plan_update(request, store, {
        "products": {"cn_a_actions": {"max_days": 28, "upstream_per_fetch": 1, "sdk_calls": 1}},
        "limits": {"approved_scope_ref": "synthetic-actions"},
    })
    out = execute_update(plan, store, tmp_path / "ledger.jsonl", cancelled=lambda: False, progress=lambda _: None)
    assert "per-request upstream metering is not established" in out["failures"][0]["reason"]
    assert out["actual_usage"]["provider_fetch"] == out["actual_usage"]["network_attempts"] == 0
