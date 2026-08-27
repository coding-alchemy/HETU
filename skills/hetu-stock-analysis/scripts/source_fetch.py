"""Fetch and save one response from the three approved public sources."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo

SOURCE_CNINFO = "cninfo-announcement-index"
SOURCE_SINA = "sina-financial-statements"
SOURCE_TENCENT = "tencent-quote-snapshot"
SOURCE_IDS = frozenset({SOURCE_CNINFO, SOURCE_SINA, SOURCE_TENCENT})
ALLOWED_HOSTS = frozenset({"www.cninfo.com.cn", "quotes.sina.cn", "qt.gtimg.cn"})
TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CNINFO_PAGES = 100


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    final_url: str
    content_type: str
    body: bytes


@dataclass(frozen=True)
class FetchRequest:
    schema_version: str
    source_id: str
    subject: str
    as_of: datetime
    purpose: str
    request: dict[str, object]


Transport = Callable[[str, str, bytes | None, dict[str, str], float], HttpResult]


class SourceFetchError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "source_id", "subject", "as_of", "purpose", "request"}
)
_PURPOSES = {
    SOURCE_CNINFO: "公告及附件",
    SOURCE_SINA: "财务报表",
    SOURCE_TENCENT: "交易状态与市场快照",
}
_REQUEST_FIELDS = {
    SOURCE_CNINFO: frozenset({"start_date", "end_date"}),
    SOURCE_SINA: frozenset({"statement", "period_count"}),
    SOURCE_TENCENT: frozenset(),
}
_STATEMENT_TYPES = {"profit": "lrb", "balance": "zcfzb", "cash": "xjllb"}
_SUBJECT_PATTERN = re.compile(r"^\d{6}\.(SZ|SH|BJ)$")
_TENCENT_PATTERN = re.compile(r'^v_([a-z]{2}\d{6})="(.*)";\s*$', re.DOTALL)


def _parse_date(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise SourceFetchError("parse_error", f"{name} must be a YYYY-MM-DD date")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise SourceFetchError(
            "parse_error", f"{name} must be a calendar YYYY-MM-DD date"
        ) from error
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def _parse_request(payload: dict[str, object], now: datetime) -> FetchRequest:
    if not isinstance(payload, dict):
        raise SourceFetchError("parse_error", "request payload must be an object")
    unknown = set(payload) - _TOP_LEVEL_FIELDS
    missing = _TOP_LEVEL_FIELDS - set(payload)
    if unknown:
        raise SourceFetchError(
            "parse_error", f"unknown top-level fields: {sorted(unknown)}"
        )
    if missing:
        raise SourceFetchError(
            "parse_error", f"missing top-level fields: {sorted(missing)}"
        )
    if payload["schema_version"] != "1":
        raise SourceFetchError("parse_error", "schema_version must be '1'")

    source_id = payload["source_id"]
    if not isinstance(source_id, str) or source_id not in SOURCE_IDS:
        raise SourceFetchError("parse_error", f"unknown source ID: {source_id}")

    subject = payload["subject"]
    if not isinstance(subject, str) or _SUBJECT_PATTERN.fullmatch(subject) is None:
        raise SourceFetchError(
            "parse_error", "subject must be a canonical A-share code"
        )

    as_of_raw = payload["as_of"]
    if not isinstance(as_of_raw, str):
        raise SourceFetchError("parse_error", "as_of must be an ISO-8601 datetime")
    try:
        as_of = datetime.fromisoformat(as_of_raw)
    except ValueError as error:
        raise SourceFetchError(
            "parse_error", "as_of must be an ISO-8601 datetime"
        ) from error
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SourceFetchError("parse_error", "as_of must include a timezone")
    if now.tzinfo is None or now.utcoffset() is None:
        raise SourceFetchError("parse_error", "now must include a timezone")
    if as_of > now:
        raise SourceFetchError("parse_error", "as_of must not be in the future")

    purpose = payload["purpose"]
    if purpose != _PURPOSES[source_id]:
        raise SourceFetchError(
            "parse_error", f"purpose must match source ID {source_id}"
        )

    request = payload["request"]
    if not isinstance(request, dict):
        raise SourceFetchError("parse_error", "request must be an object")
    allowed = _REQUEST_FIELDS[source_id]
    request_unknown = set(request) - allowed
    request_missing = allowed - set(request)
    if request_unknown:
        raise SourceFetchError(
            "parse_error", f"unknown request fields: {sorted(request_unknown)}"
        )
    if request_missing:
        raise SourceFetchError(
            "parse_error", f"missing request fields: {sorted(request_missing)}"
        )

    if source_id == SOURCE_CNINFO:
        start = _parse_date(request["start_date"], "start_date")
        end = _parse_date(request["end_date"], "end_date")
        if start > end:
            raise SourceFetchError(
                "parse_error", "start_date must not be after end_date"
            )
        if end.date() > as_of.astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise SourceFetchError("parse_error", "end_date must not exceed as_of")
    elif source_id == SOURCE_SINA:
        statement = request["statement"]
        count = request["period_count"]
        if not isinstance(statement, str) or statement not in _STATEMENT_TYPES:
            raise SourceFetchError("parse_error", "statement is not approved")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= 20
        ):
            raise SourceFetchError(
                "parse_error", "period_count must be between 1 and 20"
            )

    return FetchRequest(
        schema_version="1",
        source_id=source_id,
        subject=subject,
        as_of=as_of,
        purpose=purpose,
        request=dict(request),
    )


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise SourceFetchError(
            "transport_error", "request URL is outside the HTTPS allowlist"
        )


def _status_error(status_code: int) -> SourceFetchError:
    if status_code == 403:
        return SourceFetchError("permission_denied", "source returned HTTP 403")
    if status_code == 429:
        return SourceFetchError("rate_limited", "source returned HTTP 429")
    return SourceFetchError(
        "transport_error", f"source returned HTTP {status_code}"
    )


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: IO[bytes],
        status_code: int,
        message: str,
        headers: HTTPMessage,
        new_url: str,
    ) -> urllib.request.Request | None:
        _validate_url(new_url)
        return super().redirect_request(
            request,
            file_pointer,
            status_code,
            message,
            headers,
            new_url,
        )


def _https_request(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> HttpResult:
    _validate_url(url)
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_AllowlistRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            result = HttpResult(
                status_code=response.getcode(),
                final_url=response.geturl(),
                content_type=response.headers.get("Content-Type", ""),
                body=response.read(MAX_RESPONSE_BYTES + 1),
            )
    except urllib.error.HTTPError as error:
        raise _status_error(error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SourceFetchError("transport_error", "source transport failed") from None
    _validate_url(result.final_url)
    if len(result.body) > MAX_RESPONSE_BYTES:
        raise SourceFetchError("parse_error", "response exceeds byte limit")
    return result


def _call_transport(
    transport: Transport,
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResult:
    _validate_url(url)
    try:
        result = transport(method, url, body, headers or {}, TIMEOUT_SECONDS)
    except SourceFetchError:
        raise
    except Exception:
        raise SourceFetchError("transport_error", "source transport failed") from None
    if not isinstance(result, HttpResult):
        raise SourceFetchError("parse_error", "transport returned an invalid result")
    _validate_url(result.final_url)
    if (
        not isinstance(result.status_code, int)
        or isinstance(result.status_code, bool)
        or not isinstance(result.body, bytes)
    ):
        raise SourceFetchError("parse_error", "transport returned an invalid result")
    if len(result.body) > MAX_RESPONSE_BYTES:
        raise SourceFetchError("parse_error", "response exceeds byte limit")
    if not 200 <= result.status_code < 300:
        raise _status_error(result.status_code)
    return result


def _require_content_type(
    result: HttpResult, expected: str, source: str
) -> None:
    if not isinstance(result.content_type, str):
        raise SourceFetchError(
            "parse_error", f"{source} returned an unexpected content type"
        )
    media_type = result.content_type.partition(";")[0].strip().lower()
    if media_type != expected:
        raise SourceFetchError(
            "parse_error", f"{source} returned an unexpected content type"
        )


def _json_object(result: HttpResult, source: str) -> dict[str, Any]:
    _require_content_type(result, "application/json", source)
    try:
        value = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceFetchError("parse_error", f"{source} returned malformed JSON") from error
    if not isinstance(value, dict):
        raise SourceFetchError("parse_error", f"{source} JSON must be an object")
    return value


def _success(
    request: FetchRequest,
    captured_at: datetime,
    *,
    identity: str,
    entry: str,
    field_basis: str,
    status_code: int,
    body: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "source_id": request.source_id,
        "subject": request.subject,
        "purpose": request.purpose,
        "request": request.request,
        "source_metadata": {
            "source_id": request.source_id,
            "identity": identity,
            "entry": entry,
            "captured_at": captured_at.isoformat(),
            "field_basis": field_basis,
            "license": "public",
            "timezone": "Asia/Shanghai",
        },
        "response": {"status_code": status_code},
        "body": body,
    }


def _fetch_cninfo(
    request: FetchRequest, transport: Transport, captured_at: datetime
) -> dict[str, object]:
    identity_url = "https://www.cninfo.com.cn/new/data/szse_stock.json"
    query_url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    identity_result = _call_transport(transport, "GET", identity_url)
    identity_payload = _json_object(identity_result, "cninfo identity")
    stock_list = identity_payload.get("stockList")
    if not isinstance(stock_list, list):
        raise SourceFetchError("parse_error", "cninfo identity list is missing")
    code, exchange = request.subject.split(".")
    org_id: str | None = None
    for item in stock_list:
        if isinstance(item, dict) and item.get("code") == code:
            candidate = item.get("orgId")
            if isinstance(candidate, str) and candidate:
                org_id = candidate
                break
    if org_id is None:
        raise SourceFetchError("parse_error", "cninfo subject has no organization ID")

    pages: list[dict[str, Any]] = []
    seen_announcements: set[str] = set()
    total_bytes = 0
    expected_total: int | None = None
    for page_number in range(1, MAX_CNINFO_PAGES + 1):
        form = urllib.parse.urlencode(
            {
                "pageNum": page_number,
                "pageSize": 30,
                "column": "sse" if exchange == "SH" else "szse",
                "tabName": "fulltext",
                "plate": exchange.lower(),
                "stock": f"{code},{org_id}",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": (
                    f"{request.request['start_date']}~"
                    f"{request.request['end_date']}"
                ),
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
        ).encode("ascii")
        page_result = _call_transport(
            transport,
            "POST",
            query_url,
            form,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        total_bytes += len(page_result.body)
        if total_bytes > MAX_RESPONSE_BYTES:
            raise SourceFetchError("parse_error", "cninfo pages exceed byte limit")
        page_payload = _json_object(page_result, "cninfo page")
        if (
            not isinstance(page_payload.get("announcements"), list)
            or not isinstance(page_payload.get("totalAnnouncement"), int)
            or isinstance(page_payload.get("totalAnnouncement"), bool)
            or not isinstance(page_payload.get("hasMore"), bool)
        ):
            raise SourceFetchError("parse_error", "cninfo page shape is invalid")
        page_total = page_payload["totalAnnouncement"]
        if expected_total is None:
            expected_total = page_total
        elif page_total != expected_total:
            raise SourceFetchError("parse_error", "cninfo page totals are inconsistent")
        explicit_page = page_payload.get("pageNum")
        if explicit_page is not None and explicit_page != page_number:
            raise SourceFetchError("parse_error", "cninfo page sequence is invalid")
        for announcement in page_payload["announcements"]:
            fingerprint = json.dumps(
                announcement,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in seen_announcements:
                raise SourceFetchError(
                    "parse_error", "cninfo returned a repeated announcement"
                )
            seen_announcements.add(fingerprint)
        pages.append(page_payload)
        if page_payload["hasMore"] is False:
            returned_count = sum(len(page["announcements"]) for page in pages)
            if returned_count != expected_total:
                raise SourceFetchError(
                    "parse_error", "cninfo pagination closure count is inconsistent"
                )
            return _success(
                request,
                captured_at,
                identity="巨潮资讯网",
                entry=query_url,
                field_basis="公告查询原始分页 JSON",
                status_code=page_result.status_code,
                body={"pages": pages},
            )
    raise SourceFetchError("parse_error", "cninfo page limit reached before closure")


def _fetch_sina(
    request: FetchRequest, transport: Transport, captured_at: datetime
) -> dict[str, object]:
    code, exchange = request.subject.split(".")
    endpoint = (
        "https://quotes.sina.cn/cn/api/openapi.php/"
        "CompanyFinanceService.getFinanceReport2022"
    )
    query = urllib.parse.urlencode(
        {
            "paperCode": f"{exchange.lower()}{code}",
            "source": _STATEMENT_TYPES[str(request.request["statement"])],
            "type": 0,
            "page": 1,
            "num": request.request["period_count"],
        }
    )
    url = f"{endpoint}?{query}"
    result = _call_transport(transport, "GET", url)
    payload = _json_object(result, "sina financial statements")
    try:
        report_list = payload["result"]["data"]["report_list"]
    except (KeyError, TypeError) as error:
        raise SourceFetchError(
            "parse_error", "sina response has no report_list"
        ) from error
    if not isinstance(report_list, dict):
        raise SourceFetchError("parse_error", "sina report_list must be an object")
    return _success(
        request,
        captured_at,
        identity="新浪财经",
        entry=endpoint,
        field_basis="getFinanceReport2022 原始 report_list",
        status_code=result.status_code,
        body={
            "format": "sina_report_list",
            "result": {"data": {"report_list": report_list}},
        },
    )


def _fetch_tencent(
    request: FetchRequest, transport: Transport, captured_at: datetime
) -> dict[str, object]:
    code, exchange = request.subject.split(".")
    security = f"{exchange.lower()}{code}"
    url = f"https://qt.gtimg.cn/q={security}"
    result = _call_transport(transport, "GET", url)
    _require_content_type(result, "text/html", "tencent quote")
    try:
        raw_text = result.body.decode("gbk")
    except UnicodeDecodeError as error:
        raise SourceFetchError("parse_error", "tencent quote is not GBK text") from error
    matched = _TENCENT_PATTERN.fullmatch(raw_text)
    if matched is None or matched.group(1) != security:
        raise SourceFetchError("parse_error", "tencent quote security is invalid")
    fields = matched.group(2).split("~")
    if len(fields) <= 45 or fields[2] != code:
        raise SourceFetchError("parse_error", "tencent quote fields are incomplete")
    if not fields[3] or not fields[44] or not fields[45]:
        raise SourceFetchError("parse_error", "tencent quote labels are empty")
    try:
        trade_moment = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
    except ValueError as error:
        raise SourceFetchError(
            "parse_error", "tencent quote trade time is invalid"
        ) from error
    return _success(
        request,
        captured_at,
        identity="腾讯行情",
        entry="https://qt.gtimg.cn/",
        field_basis="腾讯行情字段索引 3、30、44、45",
        status_code=result.status_code,
        body={
            "format": "tencent_quote_snapshot",
            "raw_text": raw_text,
            "security": security,
            "trade_date": trade_moment.date().isoformat(),
            "trade_time": trade_moment.time().isoformat(),
            "timezone": "Asia/Shanghai",
            "price": fields[3],
            "price_unit": "元",
            "float_market_cap": fields[44],
            "total_market_cap": fields[45],
            "market_cap_unit": "亿元",
            "field_labels": {
                "price": "当前价",
                "float_market_cap": "流通市值",
                "total_market_cap": "总市值",
            },
        },
    )


def fetch_saved_response(
    payload: dict[str, object],
    *,
    transport: Transport = _https_request,
    now: datetime | None = None,
) -> dict[str, object]:
    captured_at = now or datetime.now().astimezone()
    request = _parse_request(payload, captured_at)
    handlers = {
        SOURCE_CNINFO: _fetch_cninfo,
        SOURCE_SINA: _fetch_sina,
        SOURCE_TENCENT: _fetch_tencent,
    }
    return handlers[request.source_id](request, transport, captured_at)


def _write_json_exclusive(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _failure_envelope(
    payload: dict[str, object], error: SourceFetchError
) -> dict[str, object]:
    source_id = payload.get("source_id")
    return {
        "schema_version": "1",
        "source_id": source_id if isinstance(source_id, str) else None,
        "status": error.status,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one approved public source response."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        print("input and output paths must differ", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output file already exists", file=sys.stderr)
        return 2
    try:
        input_bytes = input_path.read_bytes()
        payload = json.loads(input_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print(f"input file is unreadable: {input_path.name}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("input JSON must be an object", file=sys.stderr)
        return 2
    try:
        result = fetch_saved_response(payload, transport=_https_request)
    except SourceFetchError as error:
        try:
            _write_json_exclusive(output_path, _failure_envelope(payload, error))
        except (FileExistsError, OSError):
            print("output file already exists or is unwritable", file=sys.stderr)
            return 2
        return 1
    except Exception:
        error = SourceFetchError("parse_error", "unexpected fetch failure")
        try:
            _write_json_exclusive(output_path, _failure_envelope(payload, error))
        except (FileExistsError, OSError):
            print("output file already exists or is unwritable", file=sys.stderr)
            return 2
        return 1
    try:
        _write_json_exclusive(output_path, result)
    except (FileExistsError, OSError):
        print("output file already exists or is unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
