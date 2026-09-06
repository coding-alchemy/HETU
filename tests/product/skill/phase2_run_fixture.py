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
DEFAULT_RUN_ID = "合成公司-000001.SZ-standard-20260630T190000+0800"


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


def _key_value_table(
    header_key: str, header_value: str, rows: tuple[tuple[str, str], ...]
) -> str:
    lines = [f"| {header_key} | {header_value} |", "| --- | --- |"]
    lines.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(lines) + "\n"


def _required_items_table(requested_depth: str, *, unmet: bool = False) -> str:
    status = "未取得" if unmet else "有"
    impact = "未满足：限制合成结论" if unmet else "满足：无额外限制"
    return (
        "| 必需项 | 要求来源 | 状态 | 证据或查询范围 | 对结论的影响 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| 合成必需项 | {requested_depth} 共通底线 | {status} | E1，合成查询范围 | {impact} |\n"
    )


def _model_profile_table(company_model: str) -> str:
    rows = (
        ("主要经营模型", company_model),
        ("次要模型或适用分部", "无：合成单一模型"),
        ("识别依据", "E1，合成业务和财务事实"),
        ("核心经营、会计和监管特征", "合成特征"),
        ("适用方法", "合成适用方法"),
        ("易误用或不适用方法", "合成排除方法"),
        ("输入限制", "无"),
    )
    return _key_value_table("字段", "内容", rows)


def _method_selection_table(*, limited: bool = False) -> str:
    handling = "排除" if limited else "使用"
    actual_input = "未取得" if limited else "E1"
    limitation = "缺少合成关键输入" if limited else "无"
    return (
        "| 方法 | 处理 | 使用或排除理由 | 所需输入 | 实际输入 | 适用分部 | 限制 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        f"| 合成方法 | {handling} | 与合成模型匹配 | E1 | {actual_input} "
        f"| 全公司 | {limitation} |\n"
    )


def _conclusion_boundary_table(*, limited: bool = False) -> str:
    conclusion = "合成受限结论" if limited else "无"
    reason = "合成资料或方法限制" if limited else "合成输入完整"
    owner = "W5" if limited else "不适用"
    recovery = "取得合成关键输入" if limited else "不适用"
    return (
        "| 当前不能得出的结论 | 原因 | owner | 恢复条件 |\n"
        "| --- | --- | --- | --- |\n"
        f"| {conclusion} | {reason} | {owner} | {recovery} |\n"
    )


def _quality_summary_table(
    requested_depth: str,
    actual_depth: str,
    information_completeness: str,
    analysis_validity: str,
    company_model: str,
) -> str:
    rows = (
        ("请求深度", requested_depth),
        ("实际深度", actual_depth),
        ("信息完整性", information_completeness),
        ("分析有效性", analysis_validity),
        ("主要经营模型", company_model),
    )
    return _key_value_table("质量字段", "当前状态", rows)


def _report_text(
    *,
    run_id: str,
    requested_depth: str,
    actual_depth: str,
    information_completeness: str,
    analysis_validity: str,
    company_model: str,
) -> str:
    limited = information_completeness == "不完整" or analysis_validity == "受限"
    gap_row = "合成关键缺口（W5）" if limited else "无（合成 fixture）"
    conclusion_row = "合成受限结论" if limited else "无（合成 fixture）"
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
        f"| 请求深度 | {requested_depth} |",
        f"| 实际深度 | {actual_depth} |",
        f"| 信息完整性 | {information_completeness} |",
        f"| 分析有效性 | {analysis_validity} |",
        f"| 主要经营模型 | {company_model} |",
        f"| 关键资料缺口 | {gap_row} |",
        f"| 当前不能得出的结论 | {conclusion_row} |",
        "| 技术完成状态 | 无未解决技术失败 |",
        f"| 研究目录 | {run_id}/ |",
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


def _w10_text(quality_summary: str) -> str:
    return (
        "# W10 报告评审（合成）\n\n"
        + quality_summary
        + "\n报告映射表：\n\n"
        "| 报告章节 | 关键主张定位 | owner 工作包 | 证据定位 | 采用状态 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 1. 任务与时点 | 重要声明：合成内容，无研究意义。 | W0 | E1 | adopted |\n"
        "| 5. 财务验证与经营质量 | 合成占位：财务验证与经营质量。 | W5 | C1 | adopted |\n\n"
        + NO_SCRIPT_DECLARATION
        + "\n"
    )


def _manifest(
    research_root: Path,
    script_sha: str,
    *,
    run_id: str,
    requested_depth: str,
) -> dict[str, object]:
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
            "run_id": run_id,
            "requested_security": "000001.SZ",
            "verified_security": "000001.SZ",
            "as_of": "2026-06-30T18:00:00+08:00",
            "data_mode": "public",
            "requested_depth": requested_depth,
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


def build_valid_phase2_run(
    root: Path,
    *,
    run_id: str = DEFAULT_RUN_ID,
    requested_depth: str = "standard",
    actual_depth: str | None = None,
    information_completeness: str = "完整",
    analysis_validity: str = "有效",
    company_model: str = "合成经营模型（fixture）",
) -> tuple[Path, Path, Path]:
    """Return (research_root, delivery_message, lock_record) for a valid
    synthetic run laid out under ``root``."""
    if actual_depth is None:
        actual_depth = requested_depth
    incomplete = information_completeness == "不完整"
    restricted = analysis_validity == "受限"
    quality_summary = _quality_summary_table(
        requested_depth,
        actual_depth,
        information_completeness,
        analysis_validity,
        company_model,
    )
    research = root / "research" / run_id
    for sub in (
        "work-packages",
        "artifacts/raw/source-a",
        "artifacts/normalized/W5",
        "artifacts/derived/W5",
        "artifacts/scripts/W5",
    ):
        (research / sub).mkdir(parents=True, exist_ok=True)

    (research / "checkpoint.md").write_text(
        "# 检查点（合成）\n\n任务卡：合成。\n\n正式证券：000001.SZ。\n\n"
        + quality_summary,
        encoding="utf-8",
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

    (research / "report.md").write_text(
        _report_text(
            run_id=run_id,
            requested_depth=requested_depth,
            actual_depth=actual_depth,
            information_completeness=information_completeness,
            analysis_validity=analysis_validity,
            company_model=company_model,
        ),
        encoding="utf-8",
    )

    required_item_owners = ("W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8")
    method_owners = ("W5", "W6", "W7")
    for name in WORK_PACKAGES:
        package_id = name.split("-", 1)[0]
        text = f"# {name}（合成）\n\n合成占位内容。\n\n"
        if name == "W0-task-framing":
            text += "证据定位：E1。\n\n"
        if name == "W1-subject-verification":
            text += (
                "证券简称：合成公司\n\n"
                "权威身份：000001.SZ / 合成发行人 / 合成交易所（唯一核验）\n\n"
            )
        if name == "W5-financial-validation":
            text += "证据定位：C1。\n\n"
        if package_id in required_item_owners:
            unmet = name == "W5-financial-validation" and incomplete
            text += "\n" + _required_items_table(requested_depth, unmet=unmet)
        if name == "W4-business-governance":
            text += "\n" + _model_profile_table(company_model)
        if package_id in method_owners:
            limited = name == "W5-financial-validation" and restricted
            text += "\n" + _method_selection_table(limited=limited)
        if name == "W9-thesis-counterevidence":
            text += "\n" + _conclusion_boundary_table(limited=incomplete or restricted)
        if name != "W5-financial-validation":
            text += NO_SCRIPT_DECLARATION + "\n"
        (research / "work-packages" / f"{name}.md").write_text(text, encoding="utf-8")
    (research / "work-packages/W10-report-review.md").write_text(
        _w10_text(quality_summary), encoding="utf-8"
    )

    manifest = _manifest(
        research,
        sha256_file(script_rel),
        run_id=run_id,
        requested_depth=requested_depth,
    )
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
        "run_id": run_id,
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
    lock_record = root / "locks" / run_id / "lock-record.json"
    lock_record.parent.mkdir(parents=True, exist_ok=True)
    lock_record.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return research, delivery, lock_record
