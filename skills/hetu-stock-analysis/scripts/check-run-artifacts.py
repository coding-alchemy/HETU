"""Stateless single-shot checker for one finished research run.

``check_run(research_root, delivery_message, lock_record)`` mechanically
verifies structure only: required files (W0-W10, checkpoint, evidence,
manifest, report), manifest JSON parse + closed schema (top-level run block, non-empty
artifacts, per-entry closed key set, types, statuses, failure reasons and
script metadata) + entry path safety + entry/input hashes, the fixed
twelve-chapter report order with the first-chapter fixed home-metadata
fields (incl. ``分析模型``) and the chapter-2 fixed core-findings rows all
required as real Markdown table rows, the W10 report-mapping table and its
report → owner → evidence → manifest structural chain, script registration, ownership
restricted to W0-W10 directories and per-work-package no-script
declarations, and the lock record's message/research-tree hashes and
recorded paths (missing, mistyped or mismatched bindings are issues or
structured exit-2 errors, never silent passes). It
never judges natural-language truth, source applicability or adoption
decisions.

Result: ``{schema_version, mechanical_status, message_input_status,
checks, issues, warnings}``; every issue and warning carries exactly
``code``/``path``/``message``. ``mechanical_status`` is ``PASS`` only
with zero issues and a locked delivery message; warnings do not block a
pass. A missing delivery message yields
``message_input_status="not_checked"`` while mechanical checks still run.

CLI: ``check-run-artifacts.py --research-root R --delivery-message M
--lock-record L --output OUT``. Exit 0 writes a clean result; exit 1
still writes the result when issues were found; exit 2 covers argument
errors, unreadable inputs, symlinked inputs and an existing output file
(no partial artifacts). No network; no ``hetu-stock`` subcommand.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

SCHEMA_VERSION = "1.0"

CHAPTERS = (
    "任务与时点",
    "核心发现",
    "公司、业务与行业",
    "治理、审计、资本配置与重大事件",
    "财务验证与经营质量",
    "预测与受限情景",
    "估值与隐含预期",
    "市场状态与近期信号",
    "论点、反证、未知与条件",
    "监控建议",
    "数据覆盖、缺口、冲突与来源",
    "最终边界",
)

WORK_PACKAGES = (
    "W0-task-framing",
    "W1-subject-verification",
    "W2-incremental-events",
    "W3-industry-competition",
    "W4-business-governance",
    "W5-financial-validation",
    "W6-forecast-scenarios",
    "W7-valuation-expectations",
    "W8-market-signals",
    "W9-thesis-counterevidence",
    "W10-report-review",
)

W10_MAPPING_FIELDS = (
    "报告章节",
    "关键主张定位",
    "owner 工作包",
    "证据定位",
    "采用状态",
)

NO_SCRIPT_MARKER = "本工作包未创建或修改中间脚本"

FIRST_CHAPTER_FIELDS = (
    "证券、发行人、交易场所",
    "as_of",
    "分析或定稿时间",
    "推理深度",
    "数据模式",
    "请求深度、实际覆盖",
    "技术完成状态",
    "研究目录",
)

CORE_FINDING_ROWS = (
    "主体与交易状态",
    "核心业务",
    "经营规模",
    "经营回报",
    "现金或资产质量",
    "治理与重大变更",
    "并购与资本配置",
    "估值与市场状态",
    "最强反证",
    "关键验证点",
)

MANIFEST_STATUS_VALUES = ("adopted", "superseded", "failed", "not_adopted")
MANIFEST_TYPES = ("raw", "normalized", "derived", "script")
MANIFEST_TOP_KEYS = ("schema_version", "run", "artifacts")
MANIFEST_RUN_KEYS = (
    "run_id",
    "requested_security",
    "verified_security",
    "as_of",
    "data_mode",
    "requested_depth",
    "model",
    "runtime_skill",
    "created_at",
)
MANIFEST_ENTRY_BASE_KEYS = frozenset(
    {
        "path",
        "type",
        "media_format",
        "work_package",
        "sha256",
        "source_id",
        "period_or_asof",
        "created_at",
        "schema_version",
        "inputs",
        "status",
    }
)
MANIFEST_MODEL_KEYS = frozenset({"id", "reasoning_depth", "reported_by"})
MANIFEST_RUNTIME_SKILL_KEYS = frozenset({"version", "sha256"})
MANIFEST_SCRIPT_KEYS = frozenset(
    {
        "purpose",
        "safe_call",
        "dependencies",
        "environment",
        "input",
        "output",
        "exit_status",
        "executed_at",
    }
)
MANIFEST_INPUT_KEYS = frozenset({"path", "sha256"})
DATA_MODES = frozenset({"public", "authorized"})
REQUEST_DEPTHS = frozenset({"quick", "standard", "deep"})
LOCK_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "request",
        "research_root",
        "report",
        "delivery_message",
        "runtime_skill",
        "model_id",
        "environment",
        "visible_before",
        "visible_after",
        "locked_at",
        "locked_by",
    }
)
LOCK_OBJECT_FIELDS: dict[str, frozenset[str]] = {
    "request": frozenset({"path", "sha256"}),
    "research_root": frozenset({"path", "tree_sha256"}),
    "report": frozenset({"path", "sha256"}),
    "delivery_message": frozenset({"path", "sha256"}),
    "runtime_skill": frozenset({"id", "sha256"}),
    "environment": frozenset({"path", "sha256"}),
    "visible_before": frozenset({"path", "sha256"}),
    "visible_after": frozenset({"path", "sha256"}),
}
REPORT_HOME_HEADER = ("字段", "规则")
REPORT_CORE_HEADER = (
    "维度",
    "状态",
    "核心发现",
    "用户可读证据",
    "正文定位",
)
REPORT_FIXED_TABLE_HEADERS = {
    5: ("期间", "收入", "归母利润", "经营现金流", "总资产", "归母权益", "口径与单位"),
    6: ("情景", "关键变量", "显式假设", "结果或范围", "成立条件", "失效事实"),
    7: ("方法或指标", "输入与口径", "参考日", "结果", "隐含预期", "适用限制"),
    8: ("同时点价格", "股本", "市值", "交易状态", "适用估值字段"),
    10: ("指标", "口径或分母", "方向与阈值", "窗口", "连续期", "来源", "触发动作", "复查时间"),
    11: ("数据域", "状态", "来源", "时点", "替代路径", "冲突或缺口影响"),
}
REPORT_FIXED_TABLE_ALIASES = {
    5: {"口径": "口径与单位"},
    6: {"结果": "结果或范围"},
}
W10_ADOPTION_VALUES = frozenset(
    {
        "adopted",
        "superseded",
        "failed",
        "not_adopted",
        "采用",
        "已采用",
        "未采用",
        "不采用",
        "替代取得",
        "失败",
    }
)
W10_TO_MANIFEST_STATUS = {
    "adopted": "adopted",
    "采用": "adopted",
    "已采用": "adopted",
    "superseded": "superseded",
    "替代取得": "adopted",
    "failed": "failed",
    "失败": "failed",
    "not_adopted": "not_adopted",
    "未采用": "not_adopted",
    "不采用": "not_adopted",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASHARE_SECURITY_RE = re.compile(
    r"(?<![A-Za-z0-9_])\d{6}\.(?:SH|SZ|BJ)(?![A-Za-z0-9_])"
)
RUN_DIRECTORY_PATTERN = re.compile(
    r"^(?P<short_name>.+)-(?P<security>\d{6}\.(?:SZ|SH|BJ))-"
    r"(?P<depth>quick|standard|deep)-"
    r"(?P<task_time>\d{8}T\d{6}[+-]\d{4})$"
)
EVIDENCE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])([EFCJU]\d+)(?![A-Za-z0-9_])")
OWNER_RE = re.compile(r"(?<![A-Za-z0-9_])(W(?:10|[0-9]))(?![A-Za-z0-9_])")
ARTIFACT_REF_RE = re.compile(r"artifacts/[^\s`；，、）)\]}]+")
FRAGMENT_LOCATOR_RE = re.compile(
    r"evidence\.md#(.+?)(?=[；，、,;]?\s*(?:evidence\.md#|artifacts/)|$)"
)
CAPTURED_AT_RE = re.compile(r"^\d{8}T\d{6}[+-]\d{4}$")
HASH8_RE = re.compile(r"^[0-9a-f]{8}$")
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
PAIRED_RAW_HTML_RE = re.compile(
    r"<(?P<tag>[A-Za-z][A-Za-z0-9-]*)(?:\s[^<>]*?)?>"
    r"(?P<body>.*?)</(?P=tag)\s*>",
    re.DOTALL | re.IGNORECASE,
)
RAW_HTML_TAG_RE = re.compile(
    r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*?)?\s*/?>",
    re.IGNORECASE,
)
PROCESSING_INSTRUCTION_RE = re.compile(r"<\?.*?(?:\?>|$)", re.DOTALL)
CDATA_RE = re.compile(r"<!\[CDATA\[.*?(?:\]\]>|$)", re.DOTALL | re.IGNORECASE)
HTML_DECLARATION_RE = re.compile(r"<![A-Z].*?(?:>|$)", re.DOTALL)
LINK_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ ]{0,3}\[(?P<label>[^\]\n]+)\]:[^\n]*(?:\n|$)", re.MULTILINE
)


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _check(name: str, ok: bool) -> dict[str, object]:
    return {"check": name, "ok": ok}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def research_tree_sha256(research_root: Path) -> str:
    """Frozen tree hash (same algorithm as ``scripts/phase2_lock_run.py``).

    Symlinks are rejected; POSIX relative paths sort by UTF-8 bytes; for
    each regular file the digest receives relpath bytes + NUL + the file's
    SHA-256 ASCII + NUL.
    """
    digest = hashlib.sha256()
    entries: list[tuple[bytes, str]] = []
    for path in research_root.rglob("*"):
        if path.is_symlink():
            raise OSError(f"symlink inside research tree: {path}")
        if path.is_file():
            relative = path.relative_to(research_root).as_posix()
            entries.append((relative.encode("utf-8"), _sha256_file(path)))
    for name_bytes, file_sha in sorted(entries):
        digest.update(name_bytes)
        digest.update(b"\x00")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _require_regular_file(path: Path, role: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"{role} must be a regular non-symlink file: {path}")


def _safe_relative(research_root: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (research_root / candidate).resolve(strict=False)
    if not resolved.is_relative_to(research_root.resolve(strict=False)):
        return None
    return candidate


def _validate_output_location(
    output: Path, research_root: Path, lock_record: Path
) -> None:
    resolved_output = output.resolve()
    protected_roots = (research_root.resolve(), lock_record.parent.resolve())
    if any(
        resolved_output == protected or protected in resolved_output.parents
        for protected in protected_roots
    ):
        raise ValueError("output must stay outside the research and lock directories")


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _valid_timestamp(value: object) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _schema_issue(issues: list[dict[str, str]], path: str, message: str) -> None:
    issues.append(_issue("manifest.schema", path, message))


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    body = stripped[1:] if stripped.startswith("|") else stripped
    if body.endswith("|") and not body.endswith(r"\|"):
        body = body[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def _split_markdown_code(text: str) -> tuple[str, str]:
    visible: list[str] = []
    code: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    fence_indent_limit = 3
    list_content_indent: int | None = None
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        blank = "\n" if line.endswith("\n") else ""
        indentation = re.match(r"[ \t]*", content)
        assert indentation is not None
        indent_width = len(indentation.group(0).expandtabs(4))
        list_marker = re.match(
            r"(?P<indent>[ \t]*)(?P<marker>[-*+]|\d+[.)])(?P<spacing>[ \t]+)",
            content,
        )
        potential_fence = re.match(
            r"(?P<indent>[ \t]*)(?P<token>`{3,}|~{3,})",
            content,
        )
        marker = (
            potential_fence
            if potential_fence is not None
            and (
                indent_width <= 3
                or (
                    list_content_indent is not None
                    and indent_width < list_content_indent + 4
                )
            )
            else None
        )
        if fence_character is None:
            line_is_visible = False
            if marker:
                token = marker.group("token")
                info = content[marker.end() :]
                if token[0] == "`" and "`" in info:
                    visible.append(line)
                    code.append(blank)
                    continue
                fence_character = token[0]
                fence_length = len(token)
                fence_indent_limit = max(3, indent_width)
                visible.append(blank)
                code.append(blank)
            elif content.startswith("    "):
                if list_content_indent is not None and (
                    list_marker is not None or indent_width < list_content_indent + 4
                ):
                    visible.append(line)
                    code.append(blank)
                    line_is_visible = True
                else:
                    visible.append(blank)
                    code.append(line[4:])
            elif content.startswith("\t"):
                if list_content_indent is not None and (
                    list_marker is not None or indent_width < list_content_indent + 4
                ):
                    visible.append(line)
                    code.append(blank)
                    line_is_visible = True
                else:
                    visible.append(blank)
                    code.append(line[1:])
            else:
                visible.append(line)
                code.append(blank)
                line_is_visible = True
            if line_is_visible and list_marker is not None:
                list_content_indent = (
                    len(list_marker.group("indent").expandtabs(4))
                    + len(list_marker.group("marker"))
                    + len(list_marker.group("spacing").expandtabs(4))
                )
            elif line_is_visible and content.strip() and (
                list_content_indent is None or indent_width < list_content_indent
            ):
                list_content_indent = None
            continue
        closing = re.fullmatch(
            rf"(?P<indent>[ \t]*){re.escape(fence_character)}"
            rf"{{{fence_length},}}[ \t]*",
            content,
        )
        is_closing = closing is not None and len(
            closing.group("indent").expandtabs(4)
        ) <= fence_indent_limit
        visible.append(blank)
        if is_closing:
            code.append(blank)
            fence_character = None
            fence_length = 0
            fence_indent_limit = 3
        else:
            code.append(line)
    return "".join(visible), "".join(code)


def _blank_markup(text: str) -> str:
    return "".join(character if character in "\r\n" else " " for character in text)


def _without_inline_code(text: str) -> str:
    runs = list(re.finditer(r"`+", text))
    masked = list(text)
    index = 0
    while index < len(runs):
        opener = runs[index]
        backslashes = 0
        cursor = opener.start() - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2:
            index += 1
            continue
        closing_index = next(
            (
                candidate
                for candidate in range(index + 1, len(runs))
                if len(runs[candidate].group(0)) == len(opener.group(0))
            ),
            None,
        )
        if closing_index is None:
            index += 1
            continue
        closer = runs[closing_index]
        masked[opener.start() : closer.end()] = _blank_markup(
            text[opener.start() : closer.end()]
        )
        index = closing_index + 1
    return "".join(masked)


def _without_reference_definitions(text: str) -> str:
    definitions = list(LINK_REFERENCE_DEFINITION_RE.finditer(text))
    body = LINK_REFERENCE_DEFINITION_RE.sub(
        lambda match: _blank_markup(match.group(0)), text
    )

    def normalize_label(label: str) -> str:
        return " ".join(label.split()).casefold()

    used_labels = {
        normalize_label(match.group(1))
        for match in re.finditer(
            r"(?<!!)\[([^\]\n]+)\](?!\s*(?:\(|\[|:))",
            body,
        )
    }
    visible = list(text)
    for definition in definitions:
        if normalize_label(definition.group("label")) not in used_labels:
            visible[definition.start() : definition.end()] = _blank_markup(
                definition.group(0)
            )
    return "".join(visible)


def _has_visible_raw_html(text: str) -> bool:
    outside_code = _split_markdown_code(text)[0]
    without_comments = HTML_COMMENT_RE.sub(
        lambda match: _blank_markup(match.group(0)), outside_code
    )
    visible_text = _without_inline_code(without_comments)
    return any(
        pattern.search(visible_text) is not None
        for pattern in (
            RAW_HTML_TAG_RE,
            PROCESSING_INSTRUCTION_RE,
            CDATA_RE,
            HTML_DECLARATION_RE,
        )
    )


def _split_invisible_html(text: str) -> tuple[str, list[str]]:
    contents: list[str] = []

    def remove_comment(match: re.Match[str]) -> str:
        content = match.group(0)[4:]
        if content.endswith("-->"):
            content = content[:-3]
        contents.append(content)
        return _blank_markup(match.group(0))

    without_comments = HTML_COMMENT_RE.sub(remove_comment, text)

    def remove_paired_html(match: re.Match[str]) -> str:
        contents.append(match.group("body"))
        return _blank_markup(match.group(0))

    visible = PAIRED_RAW_HTML_RE.sub(remove_paired_html, without_comments)
    return visible, contents


def _without_fenced_code(text: str) -> str:
    visible = _split_invisible_html(_split_markdown_code(text)[0])[0]
    return _without_reference_definitions(visible)


def _parse_markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = text.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        header = _split_table_row(lines[index])
        separator = _split_table_row(lines[index + 1])
        if (
            header is None
            or separator is None
            or len(header) != len(separator)
            or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        ):
            index += 1
            continue
        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines):
            row = _split_table_row(lines[cursor])
            if row is None or len(row) != len(header):
                break
            rows.append(row)
            cursor += 1
        tables.append((header, rows))
        index = cursor
    return tables


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    return _parse_markdown_tables(_without_fenced_code(text))


def _find_table_rows(text: str, header: tuple[str, ...]) -> list[list[str]] | None:
    for actual_header, rows in _markdown_tables(text):
        if tuple(actual_header) == header:
            return rows
    return None


def _has_valid_contract_table(
    text: str,
    required_header: tuple[str, ...],
    aliases: dict[str, str] | None = None,
) -> bool | None:
    """Return None when no table claims this optional contract."""
    required_fields = set(required_header)
    aliases = aliases or {}

    def canonicalize(header: list[str]) -> list[str]:
        return [aliases.get(field, field) for field in header]

    def claims_contract(header: list[str]) -> bool:
        canonical = canonicalize(header)
        overlap = required_fields.intersection(canonical)
        return required_header[0] in canonical or len(overlap) >= 2

    candidates = [
        (header, rows)
        for header, rows in _markdown_tables(text)
        if claims_contract(header)
    ]
    if not candidates:
        outside_code, code_text = _split_markdown_code(text)
        _, hidden_html = _split_invisible_html(outside_code)
        hidden_blocks = [code_text, *hidden_html]
        if any(
            claims_contract(header)
            for block in hidden_blocks
            for header, _ in _parse_markdown_tables(block)
        ):
            return False
        return None
    for header, rows in candidates:
        canonical = canonicalize(header)
        if not set(required_header).issubset(canonical):
            continue
        required_indexes = [canonical.index(field) for field in required_header]
        if any(all(row[index] for index in required_indexes) for row in rows):
            return True
    return False


def _inline_code_label(cell: str) -> str:
    if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`"):
        return cell[1:-1]
    return cell


def _derived_input_hash8(input_items: object) -> str | None:
    if not isinstance(input_items, list) or not input_items:
        return None
    inputs: list[tuple[str, str]] = []
    for item in input_items:
        if not isinstance(item, dict):
            return None
        path = item.get("path")
        sha256 = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path.strip()
            or not _valid_sha256(sha256)
        ):
            return None
        inputs.append((path, sha256))
    if len(inputs) == 1:
        return inputs[0][1][:8]
    combined = "".join(
        sha256 for _, sha256 in sorted(inputs, key=lambda item: item[0])
    )
    return hashlib.sha256(combined.encode("ascii")).hexdigest()[:8]


def _artifact_name_problem(
    relative: Path, entry: dict[str, Any]
) -> tuple[str, str] | None:
    entry_type = entry.get("type")
    media_format = entry.get("media_format")
    work_package = entry.get("work_package")
    source_id = entry.get("source_id")
    schema_version = entry.get("schema_version")
    recorded_sha = entry.get("sha256")
    parts = relative.parts
    full_work_package = next(
        (name for name in WORK_PACKAGES if name.split("-", 1)[0] == work_package),
        None,
    )
    owner_directories = {
        owner
        for owner in (work_package, full_work_package)
        if isinstance(owner, str)
    }
    if not isinstance(media_format, str):
        return ("issue", "filename extension must match media_format")
    if entry_type == "normalized" and media_format not in {"json", "csv"}:
        return ("issue", "normalized media_format must be json or csv")
    if entry_type == "derived" and media_format != "json":
        return ("issue", "derived media_format must be json")
    if relative.suffix.lower() != f".{media_format.lower()}":
        return ("issue", "filename extension must match media_format")
    stem_parts = relative.name[: -len(relative.suffix)].split("--")
    if not all(stem_parts):
        return ("warning", "filename components separated by '--' should be non-empty")
    if entry_type == "raw":
        if len(parts) != 4 or parts[:2] != ("artifacts", "raw"):
            return (
                "issue",
                "raw path must be artifacts/raw/<source-id>/<contract filename>",
            )
        if not _nonempty_string(source_id) or parts[2] != source_id:
            return ("issue", "raw path must carry the recorded source_id")
        if (
            len(stem_parts) != 4
            or stem_parts[1] != source_id
            or CAPTURED_AT_RE.fullmatch(stem_parts[2]) is None
            or HASH8_RE.fullmatch(stem_parts[3]) is None
            or not isinstance(recorded_sha, str)
            or stem_parts[3] != recorded_sha[:8]
        ):
            return (
                "warning",
                "raw filename should be <dataset>--<source-id>--<captured-at>--<hash8>.<ext>",
            )
    elif entry_type == "normalized":
        period_or_asof = entry.get("period_or_asof")
        if (
            len(parts) != 4
            or parts[:2] != ("artifacts", "normalized")
            or parts[2] not in owner_directories
        ):
            return (
                "issue",
                "normalized path must be artifacts/normalized/"
                "<work-package-id>/<contract filename>",
            )
        if (
            len(stem_parts) != 4
            or not _nonempty_string(source_id)
            or stem_parts[1] != source_id
            or not _nonempty_string(period_or_asof)
            or stem_parts[2] != period_or_asof
            or stem_parts[3] != f"schema-v{schema_version}"
        ):
            return (
                "warning",
                "normalized filename must be <dataset>--<source-id>--"
                "<period-or-asof>--schema-v<version>.<json|csv>",
            )
    elif entry_type == "derived":
        expected_input_hash8 = _derived_input_hash8(entry.get("inputs"))
        if (
            len(parts) != 4
            or parts[:2] != ("artifacts", "derived")
            or parts[2] not in owner_directories
        ):
            return (
                "issue",
                "derived path must be artifacts/derived/<work-package-id>/<contract filename>",
            )
        if (
            len(stem_parts) != 3
            or HASH8_RE.fullmatch(stem_parts[1]) is None
            or expected_input_hash8 is None
            or stem_parts[1] != expected_input_hash8
            or stem_parts[2] != f"calc-v{schema_version}"
        ):
            return (
                "warning",
                "derived filename must be <calculation-or-check>--"
                "<input-hash8>--calc-v<version>.json",
            )
    elif entry_type == "script":
        if (
            len(parts) != 4
            or parts[:2] != ("artifacts", "scripts")
            or parts[2] not in owner_directories
        ):
            return (
                "issue",
                "script path must be artifacts/scripts/<work-package-id>/<contract filename>",
            )
        if (
            len(stem_parts) != 4
            or stem_parts[1] not in owner_directories
            or CAPTURED_AT_RE.fullmatch(stem_parts[2]) is None
            or HASH8_RE.fullmatch(stem_parts[3]) is None
            or not isinstance(recorded_sha, str)
            or stem_parts[3] != recorded_sha[:8]
        ):
            return (
                "warning",
                "script filename must be <purpose>--<work-package-id>--"
                "<created-at>--<hash8>.<ext>",
            )
    return None


def _check_artifact_readability(
    target_path: Path,
    relative_text: str,
    media_format: object,
    issues: list[dict[str, str]],
) -> None:
    if (
        not isinstance(media_format, str)
        or media_format not in {"json", "csv", "md", "txt", "py"}
        or not target_path.is_file()
    ):
        return
    try:
        text = target_path.read_text(encoding="utf-8")
        if media_format == "json":
            json.loads(text)
        elif media_format == "csv":
            list(csv.reader(io.StringIO(text), strict=True))
        elif media_format == "py":
            compile(text, relative_text, "exec")
    except (UnicodeError, json.JSONDecodeError, csv.Error, SyntaxError) as error:
        issues.append(
            _issue(
                "artifact.unreadable_format",
                relative_text,
                f"artifact is not readable as declared {media_format}: {type(error).__name__}",
            )
        )


def _check_manifest(
    research_root: Path,
    manifest: dict[str, Any],
    issues: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> tuple[set[str], dict[str, str]]:
    """Validate structure and entries; return registered paths and statuses."""
    registered: set[str] = set()
    entry_types: dict[str, str] = {}
    entry_statuses: dict[str, str] = {}
    input_links: list[tuple[str, str]] = []
    script_outputs: list[tuple[str, str]] = []
    top_missing = set(MANIFEST_TOP_KEYS) - set(manifest)
    top_extra = set(manifest) - set(MANIFEST_TOP_KEYS)
    if top_missing or top_extra:
        _schema_issue(
            issues,
            "manifest.json",
            "manifest top level violates the closed schema "
            f"(missing={sorted(top_missing)}, extra={sorted(top_extra)})",
        )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _schema_issue(
            issues,
            "manifest.json",
            f"manifest schema_version must be {SCHEMA_VERSION!r}",
        )
    run_block = manifest.get("run")
    if not isinstance(run_block, dict):
        _schema_issue(issues, "manifest.json", "manifest run must be an object")
    else:
        run_missing = set(MANIFEST_RUN_KEYS) - set(run_block)
        run_extra = set(run_block) - set(MANIFEST_RUN_KEYS)
        if run_missing or run_extra:
            _schema_issue(
                issues,
                "manifest.json#run",
                "manifest run block violates the closed schema "
                f"(missing={sorted(run_missing)}, extra={sorted(run_extra)})",
            )
        for key in (
            "run_id",
            "requested_security",
            "verified_security",
            "as_of",
            "created_at",
        ):
            if not _nonempty_string(run_block.get(key)):
                _schema_issue(
                    issues,
                    f"manifest.json#run.{key}",
                    f"run.{key} must be a non-empty string",
                )
        if not _valid_timestamp(run_block.get("as_of")):
            _schema_issue(
                issues,
                "manifest.json#run.as_of",
                "run.as_of must be an ISO 8601 timestamp with timezone",
            )
        if not _valid_timestamp(run_block.get("created_at")):
            _schema_issue(
                issues,
                "manifest.json#run.created_at",
                "run.created_at must be an ISO 8601 timestamp with timezone",
            )
        data_mode = run_block.get("data_mode")
        if not isinstance(data_mode, str) or data_mode not in DATA_MODES:
            _schema_issue(
                issues,
                "manifest.json#run.data_mode",
                f"run.data_mode must be one of {sorted(DATA_MODES)}",
            )
        requested_depth = run_block.get("requested_depth")
        if not isinstance(requested_depth, str) or requested_depth not in REQUEST_DEPTHS:
            _schema_issue(
                issues,
                "manifest.json#run.requested_depth",
                f"run.requested_depth must be one of {sorted(REQUEST_DEPTHS)}",
            )
        model = run_block.get("model")
        if not isinstance(model, dict) or set(model) != MANIFEST_MODEL_KEYS:
            _schema_issue(
                issues,
                "manifest.json#run.model",
                "run.model must be an object with exactly id, reasoning_depth, and reported_by",
            )
        elif not all(_nonempty_string(model.get(key)) for key in MANIFEST_MODEL_KEYS):
            _schema_issue(
                issues,
                "manifest.json#run.model",
                "every run.model value must be a non-empty string",
            )
        runtime_skill = run_block.get("runtime_skill")
        if not isinstance(runtime_skill, dict) or set(runtime_skill) != MANIFEST_RUNTIME_SKILL_KEYS:
            _schema_issue(
                issues,
                "manifest.json#run.runtime_skill",
                "run.runtime_skill must be an object with exactly version and sha256",
            )
        else:
            if not _nonempty_string(runtime_skill.get("version")):
                _schema_issue(
                    issues,
                    "manifest.json#run.runtime_skill.version",
                    "runtime Skill version must be a non-empty string",
                )
            if not _valid_sha256(runtime_skill.get("sha256")):
                _schema_issue(
                    issues,
                    "manifest.json#run.runtime_skill.sha256",
                    "runtime Skill sha256 must be 64 lowercase hexadecimal characters",
                )
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        _schema_issue(issues, "manifest.json", "artifacts must be a non-empty list")
        return registered, entry_statuses
    for index, entry in enumerate(entries):
        entry_path = f"manifest.json#artifacts[{index}]"
        if not isinstance(entry, dict):
            _schema_issue(issues, entry_path, "artifact entry must be an object")
            continue
        entry_type = entry.get("type")
        entry_status = entry.get("status")
        allowed = set(MANIFEST_ENTRY_BASE_KEYS)
        if entry_type == "script":
            allowed.add("script")
        if entry_status == "failed":
            allowed.add("failure")
        missing_keys = MANIFEST_ENTRY_BASE_KEYS - set(entry)
        extra_keys = set(entry) - allowed
        if missing_keys or extra_keys:
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                "entry key set violates the closed manifest schema "
                f"(missing={sorted(missing_keys)}, extra={sorted(extra_keys)})",
            )
        if not isinstance(entry_type, str) or entry_type not in MANIFEST_TYPES:
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                f"entry type must be one of {MANIFEST_TYPES}, got {entry_type!r}",
            )
        work_package = entry.get("work_package")
        if not isinstance(work_package, str) or work_package not in {
            name.split("-")[0] for name in WORK_PACKAGES
        }:
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                "entry work_package must be one of W0-W10",
            )
        for key in ("media_format",):
            if key in entry and not _nonempty_string(entry.get(key)):
                _schema_issue(
                    issues,
                    str(entry.get("path", entry_path)),
                    f"entry {key} must be a non-empty string",
                )
        for key in ("source_id", "period_or_asof", "schema_version"):
            if key not in entry:
                continue
            value = entry.get(key)
            if value is not None and not _nonempty_string(value):
                _schema_issue(
                    issues,
                    str(entry.get("path", entry_path)),
                    f"entry {key} must be null or a non-empty string",
                )
        if "created_at" in entry and not _valid_timestamp(entry.get("created_at")):
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                "entry created_at must be an ISO 8601 timestamp with timezone",
            )
        if entry_type in ("normalized", "derived") and not _nonempty_string(
            entry.get("schema_version")
        ):
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                f"{entry_type} entries must carry a non-empty schema_version",
            )
        if entry_type == "raw" and entry.get("schema_version") is not None:
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                "raw entries must use null schema_version",
            )
        if entry_type == "script" and entry.get("source_id") is not None:
            _schema_issue(
                issues,
                str(entry.get("path", entry_path)),
                "script entries must use null source_id",
            )
        raw_path = entry.get("path")
        relative = _safe_relative(research_root, raw_path)
        if relative is None:
            issues.append(
                _issue(
                    "manifest.unsafe_path",
                    str(raw_path),
                    "entry path must stay inside the research root without '..'",
                )
            )
            continue
        relative_text = relative.as_posix()
        if relative_text in registered:
            _schema_issue(
                issues,
                relative_text,
                "artifact paths must be unique within manifest.json",
            )
        registered.add(relative_text)
        if isinstance(entry_type, str):
            entry_types[relative_text] = entry_type
        expected_prefixes = {
            "raw": "artifacts/raw/",
            "normalized": "artifacts/normalized/",
            "derived": "artifacts/derived/",
            "script": "artifacts/scripts/",
        }
        expected_prefix = expected_prefixes.get(entry_type) if isinstance(entry_type, str) else None
        if expected_prefix and not relative_text.startswith(expected_prefix):
            _schema_issue(
                issues,
                relative_text,
                f"{entry_type} entries must live under {expected_prefix}",
            )
        name_problem = _artifact_name_problem(relative, entry)
        if name_problem is not None:
            severity, message = name_problem
            target = issues if severity == "issue" else warnings
            target.append(_issue("manifest.invalid_name", relative_text, message))
        status = entry.get("status")
        if status not in MANIFEST_STATUS_VALUES:
            issues.append(
                _issue(
                    "manifest.invalid_status",
                    relative.as_posix(),
                    f"entry status must be one of {MANIFEST_STATUS_VALUES}, got {status!r}",
                )
            )
        else:
            entry_statuses[relative_text] = status
        if status == "failed" and not (
            isinstance(entry.get("failure"), str) and entry["failure"].strip()
        ):
            issues.append(
                _issue(
                    "manifest.missing_failure_reason",
                    relative.as_posix(),
                    "failed entries must record a non-empty failure reason",
                )
            )
        if entry.get("type") == "script" and not isinstance(entry.get("script"), dict):
            issues.append(
                _issue(
                    "manifest.missing_script_metadata",
                    relative.as_posix(),
                    "script entries must carry a script metadata object",
                )
            )
        script = entry.get("script")
        if entry_type == "script" and isinstance(script, dict):
            if set(script) != MANIFEST_SCRIPT_KEYS:
                _schema_issue(
                    issues,
                    relative_text,
                    "script metadata violates the closed schema "
                    f"(missing={sorted(MANIFEST_SCRIPT_KEYS - set(script))}, "
                    f"extra={sorted(set(script) - MANIFEST_SCRIPT_KEYS)})",
                )
            for key in (
                "purpose",
                "safe_call",
                "environment",
                "executed_at",
            ):
                if not _nonempty_string(script.get(key)):
                    _schema_issue(
                        issues,
                        relative_text,
                        f"script.{key} must be a non-empty string",
                    )
            dependencies = script.get("dependencies")
            dependencies_valid = _nonempty_string(dependencies) or (
                isinstance(dependencies, list)
                and bool(dependencies)
                and all(_nonempty_string(item) for item in dependencies)
            )
            if not dependencies_valid:
                _schema_issue(
                    issues,
                    relative_text,
                    "script.dependencies must be a non-empty string or list of strings",
                )
            if not _valid_timestamp(script.get("executed_at")):
                _schema_issue(
                    issues,
                    relative_text,
                    "script.executed_at must be an ISO 8601 timestamp with timezone",
                )
            if not isinstance(script.get("exit_status"), int) or isinstance(
                script.get("exit_status"), bool
            ):
                _schema_issue(
                    issues,
                    relative_text,
                    "script.exit_status must be an integer",
                )
            elif status == "failed" and script["exit_status"] == 0:
                _schema_issue(
                    issues,
                    relative_text,
                    "failed script entries must record a nonzero script.exit_status",
                )
            elif (
                status in MANIFEST_STATUS_VALUES
                and status != "failed"
                and script["exit_status"] != 0
            ):
                _schema_issue(
                    issues,
                    relative_text,
                    "non-failed script entries must record script.exit_status=0",
                )
            for key in ("input", "output"):
                value = script.get(key)
                values = value if isinstance(value, list) else [value]
                empty_external_input = key == "input" and value == []
                if not empty_external_input and (
                    not values or not all(_nonempty_string(item) for item in values)
                ):
                    _schema_issue(
                        issues,
                        relative_text,
                        f"script.{key} must be a non-empty path string or list of paths",
                    )
                elif key == "output":
                    if status != "failed" and "not_applicable" in values:
                        warnings.append(
                            _issue(
                                "manifest.script_output_unresolved",
                                relative_text,
                                "non-failed script.output does not identify its data artifact",
                            )
                        )
                    script_outputs.extend(
                        (relative_text, str(item)) for item in values if item != "not_applicable"
                    )
        target_path = research_root / relative
        recorded_sha = entry.get("sha256")
        if not _valid_sha256(recorded_sha) or not target_path.is_file():
            issues.append(
                _issue(
                    "manifest.hash_mismatch",
                    relative.as_posix(),
                    "artifact file missing or sha256 not recorded",
                )
            )
        elif _sha256_file(target_path) != recorded_sha:
            issues.append(
                _issue(
                    "manifest.hash_mismatch",
                    relative.as_posix(),
                    "artifact sha256 differs from the recorded value",
                )
            )
        _check_artifact_readability(
            target_path,
            relative_text,
            entry.get("media_format"),
            issues,
        )
        inputs = entry.get("inputs")
        input_paths: list[str] = []
        if not isinstance(inputs, list):
            _schema_issue(
                issues,
                relative_text,
                "entry inputs must be an array",
            )
        else:
            for item in inputs:
                if not isinstance(item, dict):
                    _schema_issue(
                        issues,
                        relative_text,
                        "every inputs item must be an object",
                    )
                    continue
                if set(item) != MANIFEST_INPUT_KEYS:
                    _schema_issue(
                        issues,
                        relative_text,
                        "input items must carry exactly path and sha256",
                    )
                input_relative = _safe_relative(research_root, item.get("path"))
                input_sha = item.get("sha256")
                if input_relative is None:
                    issues.append(
                        _issue(
                            "manifest.unsafe_path",
                            str(item.get("path")),
                            "input path must stay inside the research root",
                        )
                    )
                    continue
                input_text = input_relative.as_posix()
                input_paths.append(input_text)
                input_links.append((relative_text, input_text))
                input_target = research_root / input_relative
                if not _valid_sha256(input_sha) or not input_target.is_file():
                    issues.append(
                        _issue(
                            "manifest.hash_mismatch",
                            input_relative.as_posix(),
                            "input file missing or sha256 not recorded",
                        )
                    )
                elif _sha256_file(input_target) != input_sha:
                    issues.append(
                        _issue(
                            "manifest.hash_mismatch",
                            input_relative.as_posix(),
                            "input sha256 differs from the recorded value",
                        )
                    )
        if entry_type == "script" and isinstance(script, dict):
            declared_input = script.get("input")
            declared_inputs = (
                declared_input if isinstance(declared_input, list) else [declared_input]
            )
            for item in declared_inputs:
                if _nonempty_string(item) and item != "not_applicable" and item not in input_paths:
                    _schema_issue(
                        issues,
                        relative_text,
                        f"script.input {item!r} must appear in the entry inputs array",
                    )
    for owner, link_target in input_links:
        if link_target not in registered:
            _schema_issue(
                issues,
                owner,
                f"input path {link_target!r} must reference another manifest artifact",
            )
    input_graph: dict[str, set[str]] = {path: set() for path in registered}
    for owner, link_target in input_links:
        if link_target in registered:
            input_graph[owner].add(link_target)
    visit_state: dict[str, int] = {}

    def has_input_cycle(path: str) -> bool:
        state = visit_state.get(path, 0)
        if state == 1:
            return True
        if state == 2:
            return False
        visit_state[path] = 1
        if any(has_input_cycle(input_path) for input_path in input_graph[path]):
            return True
        visit_state[path] = 2
        return False

    graph_has_cycle = any(has_input_cycle(path) for path in sorted(registered))
    if graph_has_cycle:
        issues.append(
            _issue(
                "manifest.input_cycle",
                "manifest.json",
                "artifact inputs must form an acyclic graph",
            )
        )
    else:
        source_bound: dict[str, bool] = {}

        def reaches_raw_source(path: str) -> bool:
            if path in source_bound:
                return source_bound[path]
            if entry_types.get(path) == "raw":
                source_bound[path] = True
                return True
            inputs_for_path = input_graph[path]
            result = bool(inputs_for_path) and all(
                reaches_raw_source(input_path) for input_path in inputs_for_path
            )
            source_bound[path] = result
            return result

        for path, entry_type in sorted(entry_types.items()):
            if entry_type in {"normalized", "derived"} and not reaches_raw_source(path):
                warnings.append(
                    _issue(
                        "manifest.input_not_source_bound",
                        path,
                        f"{entry_type} input chain does not terminate at a registered raw artifact",
                    )
                )
    for owner, link_target in script_outputs:
        if link_target not in registered:
            warnings.append(
                _issue(
                    "manifest.script_output_unresolved",
                    owner,
                    f"script.output {link_target!r} is not a registered manifest artifact",
                )
            )
        elif entry_types.get(link_target) not in {"raw", "normalized", "derived"}:
            _schema_issue(
                issues,
                owner,
                f"script.output {link_target!r} must reference a data artifact",
            )
    return registered, entry_statuses


def _report_sections(text: str) -> dict[int, str]:
    text = _without_fenced_code(text)
    matches = list(re.finditer(r"^## (\d+)\. ([^\n]+)$", text, flags=re.MULTILINE))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[int(match.group(1))] = text[match.start() : end]
    return sections


def _report_sections_with_code(text: str) -> dict[int, str]:
    raw_lines = text.splitlines(keepends=True)
    visible_lines = _without_fenced_code(text).splitlines(keepends=True)
    sections: dict[int, list[str]] = {}
    current: int | None = None
    for raw_line, visible_line in zip(raw_lines, visible_lines, strict=True):
        heading = re.fullmatch(r"## (\d+)\. ([^\n]+)\n?", visible_line)
        if heading:
            current = int(heading.group(1))
            sections[current] = []
        if current is not None:
            sections[current].append(raw_line)
    return {number: "".join(lines) for number, lines in sections.items()}


def _check_report(
    research_root: Path,
    expected_model_id: object,
    issues: list[dict[str, str]],
) -> None:
    report_path = research_root / "report.md"
    raw_text = report_path.read_text(encoding="utf-8")
    if _has_visible_raw_html(raw_text):
        issues.append(
            _issue(
                "report.raw_html",
                "report.md",
                "report must not contain raw HTML",
            )
        )
    text = _without_fenced_code(raw_text)
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            found.append(line[3:])
    expected = [f"{i}. {title}" for i, title in enumerate(CHAPTERS, start=1)]
    if found != expected:
        issues.append(
            _issue(
                "report.chapter_order",
                "report.md",
                "twelve fixed chapters must appear exactly once in order "
                f"(expected {expected!r}, found {found!r})",
            )
        )
    sections = _report_sections(text)
    sections_with_code = _report_sections_with_code(raw_text)
    first_chapter = sections.get(1, "")
    home_rows = _find_table_rows(first_chapter, REPORT_HOME_HEADER)
    if home_rows is None:
        home_rows = _find_table_rows(first_chapter, ("字段", "内容"))
    home_values = {
        _inline_code_label(row[0]): row[1]
        for row in (home_rows or [])
        if len(row) == 2 and row[0] and row[1]
    }
    if "分析模型" not in home_values:
        issues.append(
            _issue(
                "report.missing_model",
                "report.md",
                "first chapter must carry 分析模型 as a table row",
            )
        )
    elif _nonempty_string(expected_model_id) and home_values["分析模型"] != expected_model_id:
        issues.append(
            _issue(
                "lock.model_id_mismatch",
                "report.md",
                "report 分析模型 differs from lock model_id",
            )
        )
    for field in FIRST_CHAPTER_FIELDS:
        if field not in home_values:
            issues.append(
                _issue(
                    "report.missing_home_field",
                    "report.md",
                    f"first chapter must carry the fixed home-metadata field "
                    f"{field!r} as a table row",
                )
            )
    second_chapter = sections.get(2, "")
    core_rows = _find_table_rows(second_chapter, REPORT_CORE_HEADER)
    core_labels = {
        row[0] for row in (core_rows or []) if len(row) == len(REPORT_CORE_HEADER) and all(row)
    }
    for row in CORE_FINDING_ROWS:
        if row not in core_labels:
            issues.append(
                _issue(
                    "report.missing_core_row",
                    "report.md",
                    f"chapter 2 must carry the fixed core-findings row {row!r} as a table row",
                )
            )
    for chapter, header in REPORT_FIXED_TABLE_HEADERS.items():
        contract_table_status = _has_valid_contract_table(
            sections_with_code.get(chapter, ""),
            header,
            REPORT_FIXED_TABLE_ALIASES.get(chapter),
        )
        if contract_table_status is False:
            issues.append(
                _issue(
                    "report.missing_fixed_table",
                    "report.md",
                    f"chapter {chapter} has a contract table but does not carry all "
                    f"minimum fields {header!r} with a non-empty row",
                )
            )


def _resolved_artifact_references(
    references: list[str], registered: set[str]
) -> set[str] | None:
    cleaned = {reference.strip("`'\".。;；,，") for reference in references}
    return cleaned if cleaned and cleaned <= registered else None


def _evidence_block(text: str, reference: str) -> str | None:
    text = _without_fenced_code(text)
    heading = re.search(
        rf"^(?P<marks>#{{2,6}})\s+{re.escape(reference)}(?:\s|[（(:：—–-]|$).*$",
        text,
        flags=re.MULTILINE,
    )
    if heading:
        level = len(heading.group("marks"))
        next_heading = re.search(
            rf"^#{{1,{level}}}\s+",
            text[heading.end() :],
            flags=re.MULTILINE,
        )
        end = heading.end() + next_heading.start() if next_heading else len(text)
        return text[heading.start() : end]
    row = re.search(
        rf"^\|\s*{re.escape(reference)}\s*\|.*$",
        text,
        flags=re.MULTILINE,
    )
    if row:
        return row.group(0)
    bullet = re.search(
        rf"^(?P<indent>[ \t]*)[-*]\s+{re.escape(reference)}(?:\s|[（(:：]).*$",
        text,
        flags=re.MULTILINE,
    )
    if bullet is None:
        return None
    base_indent = len(bullet.group("indent").expandtabs(4))
    end = bullet.end()
    cursor = end
    while cursor < len(text) and text[cursor] == "\n":
        line_start = cursor + 1
        line_end = text.find("\n", line_start)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if line.strip():
            indentation = re.match(r"[ \t]*", line)
            assert indentation is not None
            if len(indentation.group(0).expandtabs(4)) <= base_indent:
                break
        end = line_end
        cursor = line_end
    return text[bullet.start() : end]


def _evidence_ref_artifacts(
    reference: str,
    evidence_text: str,
    registered: set[str],
    seen: set[str] | None = None,
) -> set[str] | None:
    visited = set() if seen is None else set(seen)
    if reference in visited:
        return None
    visited.add(reference)
    block = _evidence_block(evidence_text, reference)
    if block is None:
        return None
    artifact_refs = ARTIFACT_REF_RE.findall(block)
    if artifact_refs:
        return _resolved_artifact_references(artifact_refs, registered)
    linked_refs = {item for item in EVIDENCE_REF_RE.findall(block) if item != reference}
    resolved: set[str] = set()
    for item in linked_refs:
        linked_artifacts = _evidence_ref_artifacts(item, evidence_text, registered, visited)
        if linked_artifacts:
            resolved.update(linked_artifacts)
    return resolved or None


def _evidence_ref_resolves(
    reference: str,
    evidence_text: str,
    registered: set[str],
    seen: set[str] | None = None,
) -> bool:
    return _evidence_ref_artifacts(reference, evidence_text, registered, seen) is not None


def _fragment_artifacts(
    fragment: str, evidence_text: str, registered: set[str]
) -> set[str] | None:
    evidence_text = _without_fenced_code(evidence_text)
    normalized = " ".join(fragment.split()).casefold()
    headings = re.finditer(
        r"^(?P<marks>#{2,6})\s+(?P<title>.+)$",
        evidence_text,
        flags=re.MULTILINE,
    )
    for heading_match in headings:
        title = heading_match.group("title")
        heading_text = " ".join(title.split()).casefold()
        heading_anchor = re.sub(r"\s+", "-", title.strip()).casefold()
        if normalized in {heading_text, heading_anchor}:
            block_start = heading_match.start()
            level = len(heading_match.group("marks"))
            next_heading = re.search(
                rf"^#{{1,{level}}}\s+",
                evidence_text[heading_match.end() :],
                flags=re.MULTILINE,
            )
            block_end = (
                heading_match.end() + next_heading.start()
                if next_heading
                else len(evidence_text)
            )
            block = evidence_text[block_start:block_end]
            artifact_refs = ARTIFACT_REF_RE.findall(block)
            return _resolved_artifact_references(artifact_refs, registered)
    return None


def _fragment_resolves(fragment: str, evidence_text: str, registered: set[str]) -> bool:
    return _fragment_artifacts(fragment, evidence_text, registered) is not None


def _owner_carries_locator(
    owner_text: str,
    evidence_refs: set[str],
    artifact_refs: list[str],
    fragments: list[str],
) -> bool:
    if any(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(reference)}(?![A-Za-z0-9_])",
            owner_text,
        )
        for reference in evidence_refs
    ):
        return True
    owner_artifacts = {
        reference.strip("`'\".。;；,，")
        for reference in ARTIFACT_REF_RE.findall(owner_text)
    }
    if any(
        reference.strip("`'\".。;；,，") in owner_artifacts
        for reference in artifact_refs
    ):
        return True
    return any(f"evidence.md#{fragment}" in owner_text for fragment in fragments)


def _parse_chapter_locator(cell: str) -> tuple[list[int], str | None]:
    stripped = cell.strip()
    titled = re.fullmatch(r"(\d+)\.\s*(.+)", stripped)
    if titled is not None:
        number = int(titled.group(1))
        if not 1 <= number <= len(CHAPTERS):
            return [], "report chapter number must be between 1 and 12"
        if titled.group(2).strip() != CHAPTERS[number - 1]:
            return [number], "report chapter title does not match the fixed title"
        return [number], None
    chinese = re.fullmatch(r"第\s*(\d+)\s*章(?:\s*[：:.-]?\s*(.+))?", stripped)
    if chinese is not None:
        number = int(chinese.group(1))
        if not 1 <= number <= len(CHAPTERS):
            return [], "report chapter number must be between 1 and 12"
        title = chinese.group(2)
        if title is not None and title.strip() != CHAPTERS[number - 1]:
            return [number], "report chapter title does not match the fixed title"
        return [number], None
    range_match = re.fullmatch(
        r"(?:第\s*)?(\d+)\s*[-–—]\s*(\d+)(?:\s*章)?", stripped
    )
    if range_match is not None:
        start, end = (int(range_match.group(1)), int(range_match.group(2)))
        if not 1 <= start <= end <= len(CHAPTERS):
            return [], "report chapter range must be ascending within 1-12"
        return list(range(start, end + 1)), None
    single = re.fullmatch(r"(\d+)", stripped)
    if single is not None:
        number = int(single.group(1))
        if 1 <= number <= len(CHAPTERS):
            return [number], None
    return [], "report chapter must identify a chapter number or range within 1-12"


def _parse_owner_locator(cell: str) -> tuple[set[str], list[str]]:
    normalized = re.sub(r"\s*[-–—]\s*", "-", cell.strip())
    tokens = [
        token
        for token in re.split(r"[\s,，、;/；+&和与`]+", normalized)
        if token
    ]
    owners: set[str] = set()
    errors: list[str] = []
    for token in tokens:
        match = re.fullmatch(r"W(10|[0-9])(?:-W?(10|[0-9]))?", token)
        if match is None:
            errors.append(f"unrecognized owner locator {token!r}")
            continue
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else start
        if start > end:
            errors.append(f"owner range must be ascending, got {token!r}")
            continue
        owners.update(f"W{number}" for number in range(start, end + 1))
    if not owners:
        errors.append("owner must identify at least one W0-W10 work package")
    return owners, errors


def _check_w10_mapping(
    research_root: Path,
    registered: set[str],
    entry_statuses: dict[str, str],
    issues: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    relative_path = f"work-packages/{WORK_PACKAGES[-1]}.md"
    w10_text = (research_root / relative_path).read_text(encoding="utf-8")
    if _has_visible_raw_html(w10_text):
        warnings.append(
            _issue(
                "trace.invalid_w10_mapping",
                relative_path,
                "W10 must not use raw HTML for mapping content",
            )
        )
    rows = _find_table_rows(w10_text, W10_MAPPING_FIELDS)
    if not rows:
        warnings.append(
            _issue(
                "trace.missing_w10_mapping",
                relative_path,
                "W10 must carry one real Markdown table with the exact five-field "
                "header and at least one data row",
            )
        )
        return

    report_path = research_root / "report.md"
    evidence_path = research_root / "evidence.md"
    report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    raw_evidence_text = (
        evidence_path.read_text(encoding="utf-8") if evidence_path.is_file() else ""
    )
    if _has_visible_raw_html(raw_evidence_text):
        warnings.append(
            _issue(
                "trace.invalid_w10_mapping",
                "evidence.md",
                "evidence used by W10 must not contain raw HTML",
            )
        )
    evidence_text = _without_fenced_code(raw_evidence_text)
    report_sections = _report_sections(report_text)
    package_by_short = {name.split("-")[0]: name for name in WORK_PACKAGES}

    for row_number, row in enumerate(rows, start=1):
        row_errors: list[str] = []
        row_artifacts: set[str] = set()
        if len(row) != len(W10_MAPPING_FIELDS) or not all(row):
            row_errors.append("all five mapping cells must be non-empty")
            warnings.append(
                _issue(
                    "trace.invalid_w10_mapping",
                    f"{relative_path}#row-{row_number}",
                    "; ".join(row_errors),
                )
            )
            continue

        chapter_cell, claim_cell, owner_cell, evidence_cell, status_cell = row
        chapter_numbers, chapter_error = _parse_chapter_locator(chapter_cell)
        if chapter_error is not None:
            row_errors.append(chapter_error)
        if chapter_numbers:
            chapter_texts = [report_sections.get(number, "") for number in chapter_numbers]
            chapter_titles = {CHAPTERS[number - 1] for number in chapter_numbers}
            if claim_cell == chapter_cell or claim_cell in chapter_titles:
                row_errors.append(
                    "claim locator must be report prose, not only the chapter title"
                )
            elif not any(claim_cell in chapter_text for chapter_text in chapter_texts):
                row_errors.append(
                    "claim locator must be a searchable phrase in one selected chapter"
                )

        owners, owner_errors = _parse_owner_locator(owner_cell)
        row_errors.extend(owner_errors)
        owner_texts: dict[str, str] = {}
        for owner in sorted(owners):
            package_name = package_by_short.get(owner)
            if package_name is None:
                row_errors.append(f"unknown owner {owner!r}")
                continue
            owner_path = research_root / "work-packages" / f"{package_name}.md"
            if not owner_path.is_file():
                row_errors.append(f"owner file for {owner} is missing")
            else:
                raw_owner_text = owner_path.read_text(encoding="utf-8")
                if _has_visible_raw_html(raw_owner_text):
                    row_errors.append(f"owner {owner!r} must not contain raw HTML")
                owner_texts[owner] = _without_fenced_code(raw_owner_text)

        evidence_refs = set(EVIDENCE_REF_RE.findall(evidence_cell))
        artifact_refs = ARTIFACT_REF_RE.findall(evidence_cell)
        fragments = [
            fragment.strip(" \t`'\".。")
            for fragment in FRAGMENT_LOCATOR_RE.findall(evidence_cell)
            if fragment.strip(" \t`'\".。")
        ]
        if not evidence_refs and not artifact_refs and not fragments:
            row_errors.append("evidence locator must reference evidence.md or a manifest artifact")
        for reference in sorted(evidence_refs):
            resolved = _evidence_ref_artifacts(reference, evidence_text, registered)
            if resolved is None:
                row_errors.append(
                    f"evidence reference {reference!r} does not resolve to a manifest artifact"
                )
            else:
                row_artifacts.update(resolved)
        for artifact_ref in artifact_refs:
            if _resolved_artifact_references([artifact_ref], registered) is None:
                row_errors.append(
                    f"artifact reference {artifact_ref!r} is not registered in manifest.json"
                )
            elif artifact_ref not in evidence_text:
                row_errors.append(f"artifact reference {artifact_ref!r} is absent from evidence.md")
            else:
                row_artifacts.add(artifact_ref.strip("`'\".。;；,，"))
        for fragment in fragments:
            resolved = _fragment_artifacts(fragment, evidence_text, registered)
            if resolved is None:
                row_errors.append(
                    f"evidence.md fragment {fragment!r} does not resolve to a manifest artifact"
                )
            else:
                row_artifacts.update(resolved)

        owners_without_locator = [
            owner
            for owner, owner_text in owner_texts.items()
            if not _owner_carries_locator(
                owner_text, evidence_refs, artifact_refs, fragments
            )
        ]
        if owners_without_locator:
            row_errors.append(
                "every listed owner work package must carry the row's evidence locator; "
                f"missing={sorted(owners_without_locator)!r}"
            )

        normalized_status = re.split(r"[（(]", status_cell, maxsplit=1)[0].strip()
        if normalized_status not in W10_ADOPTION_VALUES:
            row_errors.append(f"adoption status must be controlled, got {status_cell!r}")
        else:
            expected_status = W10_TO_MANIFEST_STATUS[normalized_status]
            mismatched = {
                path: entry_statuses[path]
                for path in sorted(row_artifacts)
                if path in entry_statuses and entry_statuses[path] != expected_status
            }
            if mismatched:
                row_errors.append(
                    "adoption status must match terminal manifest artifacts; "
                    f"expected={expected_status!r}, actual={mismatched!r}"
                )

        if row_errors:
            warnings.append(
                _issue(
                    "trace.invalid_w10_mapping",
                    f"{relative_path}#row-{row_number}",
                    "; ".join(row_errors),
                )
            )


def _check_scripts(
    research_root: Path,
    registered: set[str],
    issues: list[dict[str, str]],
) -> None:
    scripts_root = research_root / "artifacts" / "scripts"
    actual: set[str] = set()
    if scripts_root.is_dir():
        for path in scripts_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                actual.add(path.relative_to(research_root).as_posix())
    for relative in sorted(actual - registered):
        issues.append(
            _issue(
                "script.unregistered",
                relative,
                "script file present under artifacts/scripts but not registered in manifest.json",
            )
        )
    owner_has_scripts: dict[str, bool] = {name: False for name in WORK_PACKAGES}
    for relative in sorted(actual):
        if relative.count("/") < 3:
            issues.append(
                _issue(
                    "script.owner_missing",
                    relative,
                    "scripts must live under artifacts/scripts/<work-package-id>/, "
                    "not directly in the scripts root",
                )
            )
    known_owners = {name for name in WORK_PACKAGES} | {name.split("-")[0] for name in WORK_PACKAGES}
    for relative in actual:
        owner = relative.split("/")[2] if relative.count("/") >= 2 else ""
        if relative.count("/") >= 3 and owner not in known_owners:
            issues.append(
                _issue(
                    "script.unknown_owner",
                    relative,
                    "scripts must live under artifacts/scripts/<W0-W10>/, "
                    f"unknown owner directory {owner!r}",
                )
            )
        for name in WORK_PACKAGES:
            if owner in (name, name.split("-")[0]):
                owner_has_scripts[name] = True
    for name in WORK_PACKAGES:
        if owner_has_scripts[name]:
            continue
        package_file = research_root / "work-packages" / f"{name}.md"
        if not package_file.is_file():
            continue  # already reported as missing.required_file
        if NO_SCRIPT_MARKER not in package_file.read_text(encoding="utf-8"):
            issues.append(
                _issue(
                    "script.missing_declaration",
                    f"work-packages/{name}.md",
                    f"work package without scripts must declare “{NO_SCRIPT_MARKER}”",
                )
            )


def _check_data_artifacts(
    research_root: Path,
    registered: set[str],
    issues: list[dict[str, str]],
) -> None:
    for artifact_type in ("raw", "normalized", "derived"):
        artifact_root = research_root / "artifacts" / artifact_type
        if not artifact_root.is_dir():
            continue
        for path in artifact_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(research_root).as_posix()
                if relative not in registered:
                    issues.append(
                        _issue(
                            "artifact.unregistered",
                            relative,
                            "data artifact is present but not registered in manifest.json",
                        )
                    )


def _check_lock_schema(
    lock: dict[str, Any], lock_record: Path, issues: list[dict[str, str]]
) -> None:
    missing = LOCK_REQUIRED_KEYS - set(lock)
    if missing:
        issues.append(
            _issue(
                "lock.schema",
                str(lock_record),
                f"lock record lacks required fields {sorted(missing)}",
            )
        )
    if lock.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            _issue(
                "lock.schema",
                str(lock_record),
                f"lock schema_version must be {SCHEMA_VERSION!r}",
            )
        )
    for key in ("run_id", "model_id", "locked_by"):
        if key in lock and not _nonempty_string(lock.get(key)):
            issues.append(
                _issue(
                    "lock.schema",
                    str(lock_record),
                    f"lock field {key!r} must be a non-empty string",
                )
            )
    if "locked_at" in lock and not _valid_timestamp(lock.get("locked_at")):
        issues.append(
            _issue(
                "lock.schema",
                str(lock_record),
                "locked_at must be an ISO 8601 timestamp with timezone",
            )
        )
    for key, required_fields in LOCK_OBJECT_FIELDS.items():
        block = lock.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise ValueError(f"lock record field {key!r} must be an object")
        block_missing = required_fields - set(block)
        if block_missing:
            issues.append(
                _issue(
                    "lock.schema",
                    str(lock_record),
                    f"lock field {key!r} lacks {sorted(block_missing)}",
                )
            )
        if key == "runtime_skill":
            if "id" in block and not _nonempty_string(block.get("id")):
                issues.append(
                    _issue(
                        "lock.schema",
                        str(lock_record),
                        "runtime_skill.id must be a non-empty string",
                    )
                )
            hash_key = "sha256"
        else:
            if "path" in block and not _nonempty_string(block.get("path")):
                issues.append(
                    _issue(
                        "lock.schema",
                        str(lock_record),
                        f"{key}.path must be a non-empty string",
                    )
                )
            hash_key = "tree_sha256" if key == "research_root" else "sha256"
        if hash_key in block and not _valid_sha256(block.get(hash_key)):
            issues.append(
                _issue(
                    "lock.schema",
                    str(lock_record),
                    f"{key}.{hash_key} must be 64 lowercase hexadecimal characters",
                )
                )


def _check_lock_auxiliary_paths(
    lock: dict[str, Any], issues: list[dict[str, str]]
) -> None:
    for field in ("request", "environment", "visible_before", "visible_after"):
        block = lock.get(field)
        if not isinstance(block, dict) or not _nonempty_string(block.get("path")):
            continue
        path = Path(str(block["path"]))
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            issues.append(
                _issue(
                    "lock.auxiliary_path_invalid",
                    str(path),
                    f"lock {field}.path must identify a real absolute file",
                )
            )
            continue
        recorded_sha = block.get("sha256")
        if _valid_sha256(recorded_sha) and _sha256_file(path) != recorded_sha:
            issues.append(
                _issue(
                    "lock.auxiliary_hash_mismatch",
                    str(path),
                    f"lock {field} file hash differs from the recorded value",
                )
            )


def _check_identity_bindings(
    manifest: dict[str, Any],
    lock: dict[str, Any],
    issues: list[dict[str, str]],
) -> None:
    run = manifest.get("run")
    if not isinstance(run, dict):
        return
    if (
        _nonempty_string(run.get("run_id"))
        and _nonempty_string(lock.get("run_id"))
        and run["run_id"] != lock["run_id"]
    ):
        issues.append(
            _issue(
                "lock.run_id_mismatch",
                "manifest.json#run.run_id",
                "manifest run_id differs from lock run_id",
            )
        )
    model = run.get("model")
    if (
        isinstance(model, dict)
        and _nonempty_string(model.get("id"))
        and _nonempty_string(lock.get("model_id"))
        and model["id"] != lock["model_id"]
    ):
        issues.append(
            _issue(
                "lock.model_id_mismatch",
                "manifest.json#run.model.id",
                "manifest model.id differs from lock model_id",
            )
        )
    runtime = run.get("runtime_skill")
    locked_runtime = lock.get("runtime_skill")
    if isinstance(runtime, dict) and isinstance(locked_runtime, dict):
        manifest_sha = runtime.get("sha256")
        lock_sha = locked_runtime.get("sha256")
        if _valid_sha256(manifest_sha) and _valid_sha256(lock_sha) and manifest_sha != lock_sha:
            issues.append(
                _issue(
                    "lock.runtime_skill_mismatch",
                    "manifest.json#run.runtime_skill.sha256",
                    "manifest runtime Skill sha256 differs from lock runtime Skill sha256",
                )
                )


def _w1_label_value(research_root: Path, label: str) -> str | None:
    relative_path = f"work-packages/{WORK_PACKAGES[1]}.md"
    path = research_root / relative_path
    if not path.is_file():
        return None
    values: list[str] = []
    for raw_line in _without_fenced_code(path.read_text(encoding="utf-8")).splitlines():
        line = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", raw_line.strip(), count=1)
        prefix = f"{label}："
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        bold = re.match(r"^\*\*(.+?)\*\*", value)
        if bold is not None:
            value = bold.group(1)
        values.append(value)
    return values[0] if len(values) == 1 and values[0] else None


def _verified_w1_short_name(research_root: Path) -> str | None:
    return _w1_label_value(research_root, "证券简称")


def _single_ashare_security(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    securities = ASHARE_SECURITY_RE.findall(value)
    return securities[0] if len(securities) == 1 else None


def _check_run_directory_name(
    research_root: Path,
    lock_record: Path,
    manifest: dict[str, Any],
    lock: dict[str, Any],
    issues: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    run = manifest.get("run")
    manifest_run_id = run.get("run_id") if isinstance(run, dict) else None
    lock_run_id = lock.get("run_id")
    # Missing or mistyped IDs are already handled by schema checks. A value
    # conflict is already handled by lock.run_id_mismatch.
    if not isinstance(manifest_run_id, str) or not isinstance(lock_run_id, str):
        return
    if manifest_run_id != lock_run_id:
        return
    if (
        manifest_run_id != research_root.name
        or lock_run_id != lock_record.parent.name
    ):
        issues.append(
            _issue(
                "run.directory_run_id_mismatch",
                str(research_root),
                "research, manifest, and lock directory run IDs must match",
            )
        )
        return
    match = RUN_DIRECTORY_PATTERN.fullmatch(research_root.name)
    short_name = _verified_w1_short_name(research_root)
    manifest_security = (
        _single_ashare_security(run.get("verified_security"))
        if isinstance(run, dict)
        else None
    )
    valid = (
        isinstance(run, dict)
        and match is not None
        and short_name is not None
        and match.group("short_name") == short_name
        and match.group("security") == manifest_security
        and match.group("depth") == run.get("requested_depth")
    )
    if not valid:
        warnings.append(
            _issue(
                "run.directory_name_noncanonical",
                str(research_root),
                "research directory should be "
                "<short-name>-<security>-<depth>-<task-time>",
            )
        )


def _verified_w1_security(research_root: Path) -> str | None:
    """Return W1's single visible, explicitly unique A-share identity."""
    identity = _w1_label_value(research_root, "权威身份")
    return _single_ashare_security(identity)


def _check_identity_metadata_mirrors(
    research_root: Path,
    manifest: dict[str, Any],
    warnings: list[dict[str, str]],
) -> None:
    """Warn when W1's verified security was not mirrored into final metadata."""
    verified_security = _verified_w1_security(research_root)
    if verified_security is None:
        warnings.append(
            _issue(
                "identity.metadata_unreadable",
                f"work-packages/{WORK_PACKAGES[1]}.md",
                "W1 exists but its single verified A-share identity cannot be parsed",
            )
        )
        return

    run = manifest.get("run")
    manifest_security = run.get("verified_security") if isinstance(run, dict) else None
    mirrors = (
        ("manifest.json#run.verified_security", manifest_security),
        ("checkpoint.md", (research_root / "checkpoint.md").read_text(encoding="utf-8")),
        (
            "report.md#1",
            _report_sections(
                _without_fenced_code(
                    (research_root / "report.md").read_text(encoding="utf-8")
                )
            ).get(1, ""),
        ),
    )
    for relative_path, mirror_value in mirrors:
        if not isinstance(mirror_value, str) or (
            "待核验" in mirror_value or verified_security not in mirror_value
        ):
            warnings.append(
                _issue(
                    "identity.metadata_out_of_sync",
                    relative_path,
                    "verified W1 identity is not synchronized to every metadata mirror",
                )
            )


def check_run(
    research_root: Path,
    delivery_message: Path,
    lock_record: Path,
) -> dict[str, object]:
    """Mechanically verify one finished run; structure only, no verdicts."""
    if research_root.is_symlink() or not research_root.is_dir():
        raise OSError(f"research root must be a real directory: {research_root}")
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, object]] = []
    lock_present = lock_record.exists()
    lock: dict[str, Any] = {}
    if lock_present:
        _require_regular_file(lock_record, "lock record")
        try:
            loaded_lock = json.loads(lock_record.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"lock record is not valid JSON: {exc}") from exc
        if not isinstance(loaded_lock, dict):
            raise ValueError("lock record must be a JSON object")
        lock = loaded_lock
        _check_lock_schema(lock, lock_record, issues)
        _check_lock_auxiliary_paths(lock, issues)
        checks.append(
            _check(
                "lock.schema",
                not any(issue["code"] == "lock.schema" for issue in issues),
            )
        )
    else:
        issues.append(
            _issue(
                "lock.not_checked",
                str(lock_record),
                "lock record absent: message consistency is not_checked and the run cannot PASS",
            )
        )
        checks.append(_check("lock.schema", False))

    message_present = delivery_message.exists()
    if message_present:
        _require_regular_file(delivery_message, "delivery message")
    message_input_status = "locked" if message_present and lock_present else "not_checked"
    if message_input_status == "not_checked":
        issues.append(
            _issue(
                "message.not_checked",
                str(delivery_message),
                "delivery message or lock record absent: "
                "the run can only be not_checked, never PASS",
            )
        )
    checks.append(_check("message.presence", message_present and lock_present))

    message_block = lock.get("delivery_message")
    if not isinstance(message_block, dict):
        message_block = {}
    research_block = lock.get("research_root")
    if not isinstance(research_block, dict):
        research_block = {}

    if lock_present:
        locked_message_path = message_block.get("path")
        if not _nonempty_string(locked_message_path):
            issues.append(
                _issue(
                    "lock.message_path_missing",
                    str(lock_record),
                    "lock record lacks a valid delivery_message.path string",
                )
            )
        elif message_present and Path(str(locked_message_path)).resolve(
            strict=False
        ) != delivery_message.resolve(strict=False):
            issues.append(
                _issue(
                    "lock.message_path_mismatch",
                    str(delivery_message),
                    "delivery message path differs from the lock record",
                )
            )
        locked_research_path = research_block.get("path")
        if not _nonempty_string(locked_research_path):
            issues.append(
                _issue(
                    "lock.research_path_missing",
                    str(lock_record),
                    "lock record lacks a valid research_root.path string",
                )
            )
        elif Path(str(locked_research_path)).resolve(strict=False) != research_root.resolve(
            strict=False
        ):
            issues.append(
                _issue(
                    "lock.research_path_mismatch",
                    str(research_root),
                    "research root path differs from the lock record",
                )
            )

    report_block = lock.get("report")
    if lock_present and isinstance(report_block, dict):
        report_path = research_root / "report.md"
        locked_report_path = report_block.get("path")
        if _nonempty_string(locked_report_path) and Path(str(locked_report_path)).resolve(
            strict=False
        ) != report_path.resolve(strict=False):
            issues.append(
                _issue(
                    "lock.report_path_mismatch",
                    str(report_path),
                    "report path differs from the lock record",
                )
            )
        recorded_report_sha = report_block.get("sha256")
        if (
            report_path.is_file()
            and _valid_sha256(recorded_report_sha)
            and _sha256_file(report_path) != recorded_report_sha
        ):
            issues.append(
                _issue(
                    "lock.report_hash_mismatch",
                    str(report_path),
                    "report hash differs from the lock record",
                )
            )

    recorded_message_sha = message_block.get("sha256")
    message_hash_ok = False
    if message_present and lock_present:
        if not isinstance(recorded_message_sha, str):
            issues.append(
                _issue(
                    "lock.message_hash_missing",
                    str(lock_record),
                    "lock record lacks a valid delivery_message.sha256 string",
                )
            )
        else:
            message_hash_ok = _sha256_file(delivery_message) == recorded_message_sha
            if not message_hash_ok:
                issues.append(
                    _issue(
                        "lock.message_hash_mismatch",
                        str(delivery_message),
                        "delivery message hash differs from the lock record",
                    )
                )
    checks.append(_check("lock.message_hash", message_hash_ok))

    recorded_tree_sha = research_block.get("tree_sha256")
    tree_hash_ok = False
    if not lock_present:
        pass
    elif not isinstance(recorded_tree_sha, str):
        issues.append(
            _issue(
                "lock.research_hash_missing",
                str(lock_record),
                "lock record lacks a valid research_root.tree_sha256 string",
            )
        )
    else:
        tree_hash_ok = research_tree_sha256(research_root) == recorded_tree_sha
        if not tree_hash_ok:
            issues.append(
                _issue(
                    "lock.research_hash_mismatch",
                    str(research_root),
                    "research tree hash differs from the lock record (files changed after locking)",
                )
            )
    checks.append(_check("lock.research_tree_hash", tree_hash_ok))

    required_directories = (
        "artifacts/raw",
        "artifacts/normalized",
        "artifacts/derived",
        "artifacts/scripts",
    )
    missing_directories = [
        relative
        for relative in required_directories
        if (research_root / relative).is_symlink()
        or not (research_root / relative).is_dir()
    ]
    for relative in missing_directories:
        issues.append(
            _issue(
                "missing.required_directory",
                relative,
                "required artifact directory is missing or is not a real directory",
            )
        )
    checks.append(_check("artifact.required_directories", not missing_directories))

    required_files = [
        "checkpoint.md",
        "evidence.md",
        "manifest.json",
        "report.md",
        *(f"work-packages/{name}.md" for name in WORK_PACKAGES),
    ]
    missing = [relative for relative in required_files if not (research_root / relative).is_file()]
    for relative in missing:
        issues.append(
            _issue(
                "missing.required_file",
                relative,
                "required run file is missing",
            )
        )
    checks.append(_check("required_files", not missing))
    for relative in required_files:
        if relative.endswith(".md") and relative not in missing:
            try:
                (research_root / relative).read_text(encoding="utf-8")
            except UnicodeError as error:
                raise ValueError(f"required Markdown is not valid UTF-8: {relative}") from error

    registered: set[str] = set()
    entry_statuses: dict[str, str] = {}
    if "manifest.json" not in missing:
        try:
            manifest = json.loads((research_root / "manifest.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues.append(
                _issue(
                    "manifest.invalid_json",
                    "manifest.json",
                    "manifest is not valid JSON",
                )
            )
        else:
            if not isinstance(manifest, dict):
                issues.append(
                    _issue(
                        "manifest.invalid_json",
                        "manifest.json",
                        "manifest must be a JSON object",
                    )
                )
            else:
                registered, entry_statuses = _check_manifest(
                    research_root, manifest, issues, warnings
                )
                if lock_present:
                    _check_identity_bindings(manifest, lock, issues)
                    if f"work-packages/{WORK_PACKAGES[1]}.md" not in missing:
                        _check_run_directory_name(
                            research_root,
                            lock_record,
                            manifest,
                            lock,
                            issues,
                            warnings,
                        )
                if (
                    "checkpoint.md" not in missing
                    and "report.md" not in missing
                    and f"work-packages/{WORK_PACKAGES[1]}.md" not in missing
                ):
                    _check_identity_metadata_mirrors(research_root, manifest, warnings)
    checks.append(
        _check(
            "manifest.entries",
            "manifest.json" not in missing
            and not any(issue["code"].startswith("manifest.") for issue in issues),
        )
    )
    checks.append(
        _check(
            "artifact.formats",
            not any(issue["code"] == "artifact.unreadable_format" for issue in issues),
        )
    )
    if "report.md" not in missing:
        _check_report(research_root, lock.get("model_id"), issues)
    checks.append(
        _check(
            "report.structure",
            not any(
                issue["code"]
                in {
                    "report.chapter_order",
                    "report.missing_model",
                    "report.missing_home_field",
                    "report.missing_core_row",
                    "report.missing_fixed_table",
                }
                for issue in issues
            ),
        )
    )
    checks.append(
        _check(
            "lock.identity",
            lock_present
            and not any(
                issue["code"]
                in {
                    "lock.run_id_mismatch",
                    "lock.model_id_mismatch",
                    "lock.runtime_skill_mismatch",
                }
                for issue in issues
            ),
        )
    )

    if f"work-packages/{WORK_PACKAGES[-1]}.md" not in missing:
        _check_w10_mapping(
            research_root, registered, entry_statuses, issues, warnings
        )
    checks.append(
        _check(
            "trace.w10_mapping",
            not any(
                issue["code"] in {"trace.missing_w10_mapping", "trace.invalid_w10_mapping"}
                for issue in issues
            ),
        )
    )

    _check_data_artifacts(research_root, registered, issues)
    _check_scripts(research_root, registered, issues)
    checks.append(
        _check(
            "script.registration",
            not any(
                issue["code"]
                in {
                    "script.unregistered",
                    "script.missing_declaration",
                    "script.owner_missing",
                    "script.unknown_owner",
                }
                for issue in issues
            ),
        )
    )

    all_checks_ok = all(bool(check["ok"]) for check in checks)
    mechanical_status = (
        "PASS" if not issues and all_checks_ok and message_input_status == "locked" else "FAIL"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mechanical_status": mechanical_status,
        "message_input_status": message_input_status,
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stateless single-shot run artifact checker")
    parser.add_argument("--research-root", required=True)
    parser.add_argument("--delivery-message", required=True)
    parser.add_argument("--lock-record", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)

    research_root = Path(arguments.research_root)
    delivery_message = Path(arguments.delivery_message)
    lock_record = Path(arguments.lock_record)
    output = Path(arguments.output)
    if output.exists():
        print("output file already exists", file=sys.stderr)
        return 2
    try:
        _validate_output_location(output, research_root, lock_record)
        result = check_run(research_root, delivery_message, lock_record)
    except (OSError, ValueError) as error:
        print(f"unreadable input: {error}", file=sys.stderr)
        return 2
    temp_output: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        _validate_output_location(output, research_root, lock_record)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_output = Path(handle.name)
            handle.write(
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            )
        assert temp_output is not None
        # link() fails atomically if the target appeared after the pre-check;
        # a mid-write failure leaves at most the temp file, never a partial
        # result at the final path.
        output.hardlink_to(temp_output)
    except (OSError, ValueError) as error:
        print(f"cannot write output: {error}", file=sys.stderr)
        return 2
    finally:
        if temp_output is not None:
            with suppress(OSError):
                temp_output.unlink(missing_ok=True)
    return 0 if result["mechanical_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
