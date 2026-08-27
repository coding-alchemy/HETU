"""Synthetic phase-2 research run fixture for the artifact checker tests.

The builder writes a structurally valid run: research tree (W0-W10,
checkpoint/evidence/manifest/report, four artifact classes), delivery
message, and a lock record whose hashes match the frozen tree-hash
contract. All content is fictional (security ``000001.SZ``, meaningless
short text); the fixture proves structure only and carries no G2 answers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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

NO_SCRIPT_DECLARATION = "本工作包未创建或修改中间脚本。"

RAW_CONTENT = json.dumps(
    {"price": "10.00", "unit": "元"}, ensure_ascii=False
) + "\n"
NORMALIZED_CONTENT = json.dumps(
    {"price": "10.00", "unit": "元", "schema_version": "1.0"},
    ensure_ascii=False,
) + "\n"
DERIVED_CONTENT = json.dumps({"check": "consistent"}, ensure_ascii=False) + "\n"
SCRIPT_CONTENT = "# 合成中间脚本（fixture，不执行）\n"

RAW_REL = (
    "artifacts/raw/source-a/quote--source-a--20260630T190500+0800--"
    f"{hashlib.sha256(RAW_CONTENT.encode()).hexdigest()[:8]}.json"
)
NORMALIZED_REL = (
    "artifacts/normalized/W5/quote--source-a--2026-06-30--schema-v1.0.json"
)
DERIVED_REL = (
    "artifacts/derived/W5/price-check--"
    f"{hashlib.sha256(NORMALIZED_CONTENT.encode()).hexdigest()[:8]}--calc-v1.0.json"
)
SCRIPT_REL = (
    "artifacts/scripts/W5/recompute--W5--20260630T190500+0800--"
    f"{hashlib.sha256(SCRIPT_CONTENT.encode()).hexdigest()[:8]}.py"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def research_tree_sha256(research_root: Path) -> str:
    """Frozen tree hash: POSIX relpath UTF-8 bytes sorted; per regular file
    write relpath bytes + NUL + file SHA-256 ASCII + NUL; symlinks rejected.
    """
    digest = hashlib.sha256()
    entries: list[tuple[bytes, str]] = []
    for path in research_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in research tree: {path}")
        if path.is_file():
            relative = path.relative_to(research_root).as_posix()
            entries.append((relative.encode("utf-8"), sha256_file(path)))
    for name_bytes, file_sha in sorted(entries):
        digest.update(name_bytes)
        digest.update(b"\x00")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _report_text() -> str:
    lines = [
        "# 合成研究报告（fixture，无研究意义）",
        "",
        f"## 1. {CHAPTERS[0]}",
        "",
        "| 字段 | 规则 |",
        "| --- | --- |",
        "| 证券、发行人、交易场所 | 000001.SZ / 合成发行人 / 合成交易所 |",
        "| as_of | 2026-06-30T18:00:00+08:00 |",
        "| 分析或定稿时间 | 2026-06-30T19:00:00+08:00 |",
        "| 分析模型 | 合成模型标识（fixture） |",
        "| 推理深度 | 未暴露 |",
        "| 数据模式 | public |",
        "| 请求深度、实际覆盖 | standard / standard |",
        "| 技术完成状态 | 无未解决技术失败 |",
        "| 研究目录 | research/ |",
        "",
        "重要声明：合成内容，无研究意义。",
        "",
    ]
    for number, title in enumerate(CHAPTERS[1:], start=2):
        lines.append(f"## {number}. {title}")
        lines.append("")
        if title == "核心发现":
            lines.append("| 维度 | 状态 | 核心发现 | 用户可读证据 | 正文定位 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for row in (
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
            ):
                lines.append(f"| {row} | 有 | 合成内容 | 合成证据 | 合成定位 |")
            lines.append("")
        fixed_tables = {
            "财务验证与经营质量": (
                ("期间", "收入", "归母利润", "经营现金流", "总资产", "归母权益", "口径与单位"),
                ("2026Q2", "1", "1", "1", "1", "1", "合成单位"),
            ),
            "预测与受限情景": (
                ("情景", "关键变量", "显式假设", "结果或范围", "成立条件", "失效事实"),
                ("合成情景", "变量", "假设", "范围", "条件", "失效"),
            ),
            "估值与隐含预期": (
                ("方法或指标", "输入与口径", "参考日", "结果", "隐含预期", "适用限制"),
                ("合成方法", "合成输入", "2026-06-30", "结果", "预期", "限制"),
            ),
            "市场状态与近期信号": (
                ("同时点价格", "股本", "市值", "交易状态", "适用估值字段"),
                ("10", "1", "10", "正常", "合成字段"),
            ),
            "监控建议": (
                (
                    "指标",
                    "口径或分母",
                    "方向与阈值",
                    "窗口",
                    "连续期",
                    "来源",
                    "触发动作",
                    "复查时间",
                ),
                ("指标", "口径", "阈值", "窗口", "一期", "合成来源", "复查", "2026-07-01"),
            ),
            "数据覆盖、缺口、冲突与来源": (
                ("数据域", "状态", "来源", "时点", "替代路径", "冲突或缺口影响"),
                ("合成域", "有", "合成来源", "2026-06-30", "不适用", "无"),
            ),
        }
        if title in fixed_tables:
            header, row = fixed_tables[title]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            lines.append("| " + " | ".join(row) + " |")
            lines.append("")
        lines.append(f"合成占位：{title}。")
        lines.append("")
    return "\n".join(lines)


def _w10_text() -> str:
    return (
        "# W10 报告评审（合成）\n\n"
        "报告映射表：\n\n"
        "| 报告章节 | 关键主张定位 | owner 工作包 | 证据定位 | 采用状态 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 1. 任务与时点 | 重要声明：合成内容，无研究意义。 | W0 | E1 | adopted |\n"
        "| 5. 财务验证与经营质量 | 合成占位：财务验证与经营质量。 | W5 | C1 | adopted |\n\n"
        + NO_SCRIPT_DECLARATION
        + "\n"
    )


def _manifest(research_root: Path, script_sha: str) -> dict[str, object]:
    raw_rel = RAW_REL
    normalized_rel = NORMALIZED_REL
    derived_rel = DERIVED_REL
    script_rel = SCRIPT_REL
    raw_sha = sha256_file(research_root / raw_rel)
    normalized_sha = sha256_file(research_root / normalized_rel)
    derived_sha = sha256_file(research_root / derived_rel)
    created = "2026-06-30T19:05:00+08:00"
    return {
        "schema_version": "1.0",
        "run": {
            "run_id": "synthetic-run-0001",
            "requested_security": "000001.SZ",
            "verified_security": "000001.SZ",
            "as_of": "2026-06-30T18:00:00+08:00",
            "data_mode": "public",
            "requested_depth": "standard",
            "model": {
                "id": "合成模型标识（fixture）",
                "reasoning_depth": "未暴露",
                "reported_by": "host",
            },
            "runtime_skill": {"version": "synthetic", "sha256": "0" * 64},
            "created_at": created,
        },
        "artifacts": [
            {
                "path": raw_rel,
                "type": "raw",
                "media_format": "json",
                "work_package": "W5",
                "sha256": raw_sha,
                "source_id": "source-a",
                "period_or_asof": "2026-06-30",
                "created_at": created,
                "schema_version": None,
                "inputs": [],
                "status": "adopted",
            },
            {
                "path": normalized_rel,
                "type": "normalized",
                "media_format": "json",
                "work_package": "W5",
                "sha256": normalized_sha,
                "source_id": "source-a",
                "period_or_asof": "2026-06-30",
                "created_at": created,
                "schema_version": "1.0",
                "inputs": [{"path": raw_rel, "sha256": raw_sha}],
                "status": "adopted",
            },
            {
                "path": derived_rel,
                "type": "derived",
                "media_format": "json",
                "work_package": "W5",
                "sha256": derived_sha,
                "source_id": None,
                "period_or_asof": "2026-06-30",
                "created_at": created,
                "schema_version": "1.0",
                "inputs": [{"path": normalized_rel, "sha256": normalized_sha}],
                "status": "adopted",
            },
            {
                "path": script_rel,
                "type": "script",
                "media_format": "py",
                "work_package": "W5",
                "sha256": script_sha,
                "source_id": None,
                "period_or_asof": "not_applicable",
                "created_at": created,
                "schema_version": "1.0",
                "inputs": [{"path": raw_rel, "sha256": raw_sha}],
                "status": "adopted",
                "script": {
                    "purpose": "合成重算（fixture）",
                    "safe_call": f"python {SCRIPT_REL}",
                    "dependencies": "标准库",
                    "environment": "合成环境",
                    "input": raw_rel,
                    "output": derived_rel,
                    "exit_status": 0,
                    "executed_at": created,
                },
            },
        ],
    }


def build_valid_phase2_run(root: Path) -> tuple[Path, Path, Path]:
    """Return (research_root, delivery_message, lock_record) for a valid
    synthetic run laid out under ``root``."""
    research = root / "research"
    for sub in (
        "work-packages",
        "artifacts/raw/source-a",
        "artifacts/normalized/W5",
        "artifacts/derived/W5",
        "artifacts/scripts/W5",
    ):
        (research / sub).mkdir(parents=True, exist_ok=True)

    (research / "checkpoint.md").write_text(
        "# 检查点（合成）\n\n任务卡：合成。\n", encoding="utf-8"
    )
    (research / "evidence.md").write_text(
        "# 证据（合成）\n\n"
        "### E1 时点\n\n"
        f"- 合成时点证据：`{RAW_REL}`。\n\n"
        "### C1 财务校验\n\n"
        f"- 合成计算证据：`{DERIVED_REL}`。\n",
        encoding="utf-8",
    )

    raw_rel = research / RAW_REL
    raw_rel.write_text(RAW_CONTENT, encoding="utf-8")
    normalized_rel = research / NORMALIZED_REL
    normalized_rel.write_text(NORMALIZED_CONTENT, encoding="utf-8")
    derived_rel = research / DERIVED_REL
    derived_rel.write_text(DERIVED_CONTENT, encoding="utf-8")
    script_rel = research / SCRIPT_REL
    script_rel.write_text(SCRIPT_CONTENT, encoding="utf-8")

    (research / "report.md").write_text(_report_text(), encoding="utf-8")

    for name in WORK_PACKAGES:
        text = f"# {name}（合成）\n\n合成占位内容。\n\n"
        if name == "W0-task-framing":
            text += "证据定位：E1。\n\n"
        if name == "W5-financial-validation":
            text += "证据定位：C1。\n\n"
        if name != "W5-financial-validation":
            text += NO_SCRIPT_DECLARATION + "\n"
        (research / "work-packages" / f"{name}.md").write_text(text, encoding="utf-8")
    (research / "work-packages/W10-report-review.md").write_text(_w10_text(), encoding="utf-8")

    manifest = _manifest(research, sha256_file(script_rel))
    (research / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    delivery = root / "delivery-message.md"
    delivery.write_text("合成最终消息：报告已定稿（fixture，无事实内容）。\n", encoding="utf-8")

    request = root / "request.md"
    request.write_text("合成任务请求（fixture）。\n", encoding="utf-8")
    environment = root / "environment.json"
    environment.write_text(
        json.dumps({"python": "synthetic"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    visible_before = root / "visible-before.txt"
    visible_before.write_text("运行前可见文件（合成）\n", encoding="utf-8")
    visible_after = root / "visible-after.txt"
    visible_after.write_text("运行后可见文件（合成）\n", encoding="utf-8")

    lock = {
        "schema_version": "1.0",
        "run_id": "synthetic-run-0001",
        "request": {"path": str(request.resolve()), "sha256": sha256_file(request)},
        "research_root": {
            "path": str(research.resolve()),
            "tree_sha256": research_tree_sha256(research),
        },
        "report": {
            "path": str((research / "report.md").resolve()),
            "sha256": sha256_file(research / "report.md"),
        },
        "delivery_message": {
            "path": str(delivery.resolve()),
            "sha256": sha256_file(delivery),
        },
        "runtime_skill": {"id": "hetu-stock-analysis", "sha256": "0" * 64},
        "model_id": "合成模型标识（fixture）",
        "environment": {
            "path": str(environment.resolve()),
            "sha256": sha256_file(environment),
        },
        "visible_before": {
            "path": str(visible_before.resolve()),
            "sha256": sha256_file(visible_before),
        },
        "visible_after": {
            "path": str(visible_after.resolve()),
            "sha256": sha256_file(visible_after),
        },
        "locked_at": "2026-06-30T19:10:00+08:00",
        "locked_by": "fixture",
    }
    lock_record = root / "lock-record.json"
    lock_record.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return research, delivery, lock_record
