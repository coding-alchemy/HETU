"""有界Tushare核验及获批目录发现；原响应与计量分开保留，不生成信号。"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import ssl
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

APIS = {"stock_basic", "bse_mapping", "namechange", "trade_cal", "bak_basic", "daily",
        "adj_factor", "daily_basic", "dividend", "suspend_d", "stock_st", "stk_limit",
        "index_member_all", "sw_daily"}
ROOT = Path(__file__).resolve().parents[2]


def load_token():
    """凭据仅在执行时注入，不source本地文件或记录其内容。"""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        path = ROOT / ".hetu/credentials/tushare.env"
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("credential file must be private and not a symlink")
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "TUSHARE_TOKEN":
                token = value.strip().strip("\"'")
    if not token or any(c.isspace() for c in token):
        raise ValueError("TUSHARE_TOKEN is missing or malformed")
    return token


class HTTPTransport:
    """固定官方主机，证书校验；无SDK、代理、自动重试、跳转或翻页。"""

    def send(self, api, params, fields, token):
        connection = http.client.HTTPSConnection("api.tushare.pro", timeout=30,
                                                 context=ssl.create_default_context())
        payload = json.dumps({"api_name": api, "params": params, "fields": fields, "token": token})
        try:
            connection.request("POST", "/", body=payload.encode(), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()


def wait_for_rate(previous, rate):
    if previous is not None:
        time.sleep(max(0, 60 / rate + 0.05 - (time.monotonic() - previous)))


def _save(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _append(path, data):
    with path.open("a") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _classify(status, body, request):
    if 300 <= status < 400:
        return "redirect_blocked", None
    if status in (401, 403):
        return "authentication_blocked", None
    if status == 429:
        return "rate_limited", None
    if status >= 500:
        return "temporary_failure", None
    if status != 200:
        return "http_error", None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return "invalid_response", None
    if not isinstance(data, dict):
        return "invalid_response", None
    if data.get("code") != 0:
        message = str(data.get("msg", "")).lower()
        if data.get("code") == -2001 or "token" in message:
            return "authentication_blocked", None
        if any(s in message for s in ("每分钟", "每小时", "每天", "频率", "频次", "限流", "too many")):
            return "rate_limited", None
        if any(s in message for s in ("权限", "积分", "permission")):
            return "permission_blocked", None
        return "provider_error", None
    table = data.get("data")
    if (not isinstance(table, dict) or not isinstance(table.get("fields"), list)
            or not isinstance(table.get("items"), list)):
        return "schema_gap", None
    fields, rows = table["fields"], table["items"]
    if data.get("has_more") is True or table.get("has_more") is True:
        return "pagination_unproven", len(rows)
    if not set(request["fields"].split(",")).issubset(fields):
        return "schema_gap", len(rows)
    if any(not isinstance(row, list) or len(row) != len(fields) for row in rows):
        return "schema_gap", len(rows)
    limit = request.get("single_call_row_limit")
    if limit is not None and len(rows) >= limit:
        return "row_limit_reached", len(rows)
    return "success" if rows else "success_empty", len(rows)


def probe_source(scope_path: Path, output_dir: Path, *, execute: bool = False) -> dict:
    """固定清单串行执行；先落尝试账，重开不重取终态或不确定请求。"""
    scope_path, output_dir = Path(scope_path).resolve(), Path(output_dir).resolve()
    scope = json.loads(scope_path.read_text())
    request_path = scope_path.with_name("request-list.json")
    requests = json.loads(request_path.read_text())["requests"]
    if not execute:
        return {"status": "plan_only", "planned_requests": len(requests), "attempts": 0}
    directory = scope.get("kind") == "stock_basic_directory"
    auth_path = (scope_path.with_name("authorization.json") if directory
                 else scope_path.parent.parent / "g1-authorization-20261024.json")
    auth = json.loads(auth_path.read_text())
    if not auth.get("directory_approved" if directory else "g1_approved"):
        raise ValueError("directory is not approved" if directory else "G1 is not approved")
    if ((auth_path.parent / auth["scope_ref"]).resolve() != scope_path
            or (auth_path.parent / auth["requests_ref"]).resolve() != request_path):
        raise ValueError("scope does not match approved references")
    budget, approved = scope["budget"], auth["approved_limits"]
    maximum = min(budget["total_product_call_attempts_max"], approved["total_http_product_attempts_max"],
                  9 if directory else 45)
    retry_max = 0 if directory else min(budget["max_retry_attempts"], approved["shared_retry_reserve"], 4)
    rate = min(budget["global_rate_max_per_minute"], 30)
    if directory:
        from hetu_quant.providers.tushare import FIELDS, TushareProvider, directory_calls

        expected = directory_calls()
        actual = [(r["api"], r["params"]) for r in requests]
        if (actual != expected or any(r["fields"] != FIELDS["stock_basic"]
                                     or r.get("single_call_row_limit") != 6000 for r in requests)):
            raise ValueError("directory queries exceed approved scope")
        codes = set()
    else:
        codes = {x["code"] for x in scope["objects"]} | {scope["industry_case"]["code"]}
    if (not directory and (len(scope["objects"]) > 8 or len(requests) > 41)) or rate <= 0:
        raise ValueError("scope exceeds approved limits")
    ids = [r["id"] for r in requests]
    if len(ids) != len(set(ids)) or any(not i.isalnum() for i in ids):
        raise ValueError("invalid request identities")
    for request in requests:
        if request["api"] not in APIS or "token" in request["params"]:
            raise ValueError("unsupported product or credential in scope")
        for key in ("ts_code", "n_code"):
            if key in request["params"] and request["params"][key] not in codes:
                raise ValueError("object outside approved scope")
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = output_dir / "sample-ledger.jsonl"
    events = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    started = [e for e in events if e["event"] == "started"]
    finished = [e for e in events if e["event"] == "finished"]
    summary = {"status": "completed", "attempts": len(started), "retries": sum(e["retry"] for e in started),
               "rows": sum(e.get("rows") or 0 for e in finished), "results": finished,
               "sdk_calls": 0, "provider_fetch": 0, "auth_calls": 0, "supplier_quota_bytes": None,
               "supplier_failed_attempt_charging": "unknown", "hidden_transmissions": "unknown",
               "skipped_requests": [], "data_fee_cny": 0, "real_research_runs": 0}
    if {e["attempt"] for e in started} != {e["attempt"] for e in finished}:
        summary["status"] = "interrupted_attempt_unknown"
        _save(output_dir / "summary.json", summary)
        return summary
    for event in finished:
        if event.get("raw_ref"):
            raw_path = output_dir / event["raw_ref"]
            if (not raw_path.is_file()
                    or hashlib.sha256(raw_path.read_bytes()).hexdigest() != event["sha256_saved"]):
                raise ValueError("previous raw artifact changed; do not reacquire automatically")
    snapshot = output_dir / "executed-requests.json"
    if snapshot.exists() and json.loads(snapshot.read_text()) != requests:
        raise ValueError("same execution request artifact was modified; do not repeat prior calls")
    _save(snapshot, requests)
    blocked = {e["api"] for e in finished if e["status"] not in {"success", "success_empty", "temporary_failure",
                                                                           "network_failure"}}
    global_stops = {"authentication_blocked", "rate_limited", "redirect_blocked", "secret_echo_blocked"}
    prior_global = next((e["status"] for e in finished if e["status"] in global_stops), None)
    if prior_global:
        summary["status"] = prior_global
        _save(output_dir / "summary.json", summary)
        return summary
    done = {e["id"] for e in finished}
    token, transport = load_token(), HTTPTransport()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    previous = None
    for request in requests:
        if request["id"] in done:
            continue
        if request["api"] in blocked:
            summary["skipped_requests"].append(request["id"])
            continue
        for retry in (False, True):
            if summary["attempts"] >= maximum:
                summary["status"] = "budget_exhausted"
                break
            if retry:
                summary["retries"] += 1
            wait_for_rate(previous, rate)
            previous = time.monotonic()
            summary["attempts"] += 1
            entry = {"event": "started", "attempt": summary["attempts"], "id": request["id"],
                     "api": request["api"], "params": request["params"], "fields": request["fields"],
                     "retry": retry, "at": datetime.now(UTC).isoformat()}
            _append(ledger, entry)
            result = {**entry, "event": "finished", "rows": None, "raw_ref": None}
            try:
                status, body = transport.send(request["api"], request["params"], request["fields"], token)
            except (OSError, http.client.HTTPException) as error:
                result.update(status="network_failure", error_type=type(error).__name__)
            else:
                result["http_status"] = status
                result["sha256_received"] = hashlib.sha256(body).hexdigest()
                echoed = token.encode() in body
                if echoed:
                    body = body.replace(token.encode(), b"[REDACTED]")
                raw = raw_dir / f"{summary['attempts']:02d}-{request['id']}.json"
                with raw.open("xb") as stream:
                    stream.write(body)
                result.update(raw_ref=str(raw.relative_to(output_dir)), raw_bytes=len(body),
                              sha256_saved=hashlib.sha256(body).hexdigest(), secret_redacted=echoed)
                result["status"], result["rows"] = _classify(status, body, request)
                if directory and result["status"] in {"success", "success_empty"}:
                    try:
                        TushareProvider(client=object()).decode_response(
                            body, api=request["api"], params=request["params"], obtained_at=entry["at"])
                    except ValueError:
                        result["status"] = "directory_slice_unproven"
                if echoed:
                    result["status"] = "secret_echo_blocked"
            result["finished_at"] = datetime.now(UTC).isoformat()
            _append(ledger, result)
            summary["results"].append(result)
            summary["rows"] += result["rows"] or 0
            _save(output_dir / "summary.json", summary)
            if result["status"] in global_stops:
                summary["status"] = result["status"]
                break
            if result["status"] in {"temporary_failure", "network_failure"}:
                if not retry and summary["retries"] < retry_max and summary["attempts"] < maximum:
                    continue
            elif result["status"] not in {"success", "success_empty"}:
                blocked.add(request["api"])
            break
        if summary["status"] in global_stops or summary["status"] == "budget_exhausted":
            break
    if (summary["status"] not in global_stops and summary["attempts"] >= maximum
            and len({e["id"] for e in summary["results"]}) < len(requests)):
        summary["status"] = "budget_exhausted"
    if directory and summary["status"] == "completed" and (
            len(summary["results"]) != 9 or any(e["status"] not in {"success", "success_empty"}
                                                for e in summary["results"])):
        summary["status"] = "limited"
    _save(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = probe_source(args.scope, args.output, execute=args.execute)
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False))
