"""Stage-04 task 04.1 static prompt-contract tests.

Assert the text invariants of the formal-subtask and parallel-dispatch
contract added to the canonical Skill references by phase-5 task 04.1:
subtask identity, independent lifecycle and attempt/adoption records that
reuse the stage-03 contract without touching the catalog or core
dependencies, the parallel evaluation threshold, the six pre-dispatch
unifications, the seven default-serial stages, the twelve self-contained
input items, the thirteen return items and the five-file write prohibition
with serial merge. Dispatch behavior samples (P01-P06) are exercised
separately by the controller.

Task 04.2 appends the confluence-adjudication / failure-isolation /
adoption contract invariants (orchestration "汇合裁决与失败隔离",
recovery "并行失败隔离与越权处置") and the shape assertions for the
synthetic P03-P06 fixtures under ``tests/product/fixtures/phase5_parallel/``.

Task 04.3 appends the evidence write-back assertions: the stage-04
write-back section in the stage-02 impact map (four columns, per-row
audit with no new implementation, positive/negative case reference list
whose test names must exist in the cited files, and the fixed stage-07
ordered/parallel comparison contract with pending numbers), plus the
tool-failure / deterministic-helper boundary clauses that already exist
in the canonical references.
"""

import json
import re
from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")
ORCHESTRATION = "references/orchestration.md"
HOST_TOOLS = "references/host-tools.md"
RESULT = "references/work-package-result.md"
RECOVERY = "references/recovery.md"
TOOL_CATALOG = "references/tool-catalog.md"
EVIDENCE_RULES = "references/evidence-rules.md"

IMPACT_MAP = Path(".hetu/validation/phase-5/20260909-stage02-calibration/impact-map.md")
WRITEBACK_HEADING = "## 5. 阶段 04 实现回写（2026-09-12）"

NEW_HEADING = "## 正式子任务与并行派发"
TEMP_HEADING = "## 临时研究子问题"
NEXT_HEADING = "## W2/W5 共享基准"
HOST_HEADING = "## 子任务派发与最小权限"

ADJUDICATION_HEADING = "## 汇合裁决与失败隔离"
ISOLATION_HEADING = "## 并行失败隔离与越权处置"
REUSE_HEADING = "## 旧任务资料复用与独立副本"
RESUME_HEADING = "## 同一任务恢复"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "phase5_parallel"

TEMP_SECTION_FLOW = (
    "Agent可把工作包临时拆成聚焦研究子问题并把结果汇回原包。临时问题没有稳定ID、独立覆盖"
    "状态或跨任务生命周期，不进入catalog，不改变核心依赖和最低覆盖，也不能演变为代码执行DAG。"
)

OWNER_ARTIFACT_DIRS = (
    "artifacts/raw/<source-id>/",
    "artifacts/normalized/<owner>/",
    "artifacts/derived/<owner>/",
    "artifacts/scripts/<owner>/",
)

UNIFIED_ITEMS = (
    "证券／发行人",
    "`as_of`",
    "数据模式",
    "深度",
    "关注点",
    "授权与证据规则",
)

SERIAL_ITEMS = (
    "请求规范化",
    "依赖财务基线的预测估值",
    "跨域综合",
    "最大未知",
    "监控",
    "质量复核",
    "报告",
)

INPUT_ITEMS = (
    "目标",
    "主体",
    "时点",
    "模式",
    "授权",
    "深度",
    "来源期望",
    "禁止项",
    "完成条件",
    "只读输入",
    "可写文件",
    "返回格式",
)

RETURN_ITEMS = (
    "任务标识",
    "查询摘要",
    "来源定位",
    "事件／发布时间",
    "事实",
    "计算输入",
    "判断",
    "反证",
    "冲突",
    "缺口",
    "不确定性",
    "后续验证",
    "产物与失败登记",
)

SHARED_FILES = ("`manifest.json`", "`checkpoint.md`", "`evidence.md`", "owner正文", "最终报告")

REVIEW_DIMENSIONS = ("完成度", "来源", "时点", "授权", "证据类型", "结论边界")
FAILURE_OPTIONS = ("重试", "缩小", "重新分派", "顺序执行", "局部降级")
AUTHORIZED_RECHECK = ("实际来源", "数据集", "许可", "点时", "必要性", "脱敏")


def _read(relative: str) -> str:
    return ROOT.joinpath(relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    tail = text[start:]
    boundary = tail.find("\n## ")
    return tail if boundary == -1 else tail[:boundary]


def _flow(text: str) -> str:
    """Collapse all whitespace so hard-wrapped phrases match as one string."""
    return re.sub(r"\s+", "", text)


def _dispatch_section() -> str:
    return _section(_read(ORCHESTRATION), NEW_HEADING)


def _numbered_list_positions(raw: str, items: tuple[str, ...]) -> list[int]:
    positions = []
    for number, item in enumerate(items, start=1):
        match = re.search(rf"^{number}\. {item}。$", raw, re.MULTILINE)
        assert match is not None, item
        positions.append(match.start())
    return positions


def test_new_section_sits_after_temporary_subquestions_without_rewriting_them() -> None:
    text = _read(ORCHESTRATION)

    assert _flow(_section(text, TEMP_HEADING)) == TEMP_SECTION_FLOW
    assert text.index(TEMP_HEADING) < text.index(NEW_HEADING) < text.index(NEXT_HEADING)


def test_formal_subtask_identity_lifecycle_and_adoption_reuse_existing_contract() -> None:
    section = _flow(_dispatch_section())

    assert "正式子任务有稳定任务标识和父子关系记录" in section
    assert "独立生命周期" in section
    assert "任务关系、尝试和`provenance`条款" in section
    assert "每次尝试记录旧新差异、原因和失败" in section
    assert "采用产物唯一" in section
    assert "失败与未采用版本可追溯" in section
    assert "不改变W0–W10身份、核心依赖与最低覆盖" in section
    assert "不进入catalog" in section
    assert "临时问题也不得注册为新的核心工作包" in section


def test_subtask_artifacts_stay_in_owner_dirs_named_by_task_and_attempt() -> None:
    section = _flow(_dispatch_section())

    for directory in OWNER_ARTIFACT_DIRS:
        assert directory in section, directory
    assert "以任务及尝试标识区分文件" in section
    assert "不新增目录层级" in section
    assert "不覆盖共享文件" in section


def test_parallel_threshold_requires_independent_stateless_problems() -> None:
    section = _flow(_dispatch_section())

    assert "只有两个及以上可独立理解、取证、产出且无共享可变状态的独立问题才评估并行" in section
    assert "是否派发由主Agent判断决定" in section


def test_predispatch_unifies_six_items_and_selects_domains_by_question() -> None:
    raw = _dispatch_section()
    section = _flow(raw)
    unified = _flow(re.search(r"派发前[\s\S]*?授权与证据规则。", raw).group(0))

    positions = [unified.index(item) for item in UNIFIED_ITEMS]
    assert positions == sorted(positions)
    assert "候选域按本次实际问题选择" in section
    assert "不按固定领域整组启动" in section
    assert "不为凑固定域数拆分或重复取证" in section
    assert "“旧任务资料复用与独立副本”的复用条款" in section
    assert "只派发仍需解决的问题" in section
    assert "复用不省略剩余必需研究与独立核验" in section


def test_seven_stages_default_serial_and_dependencies_come_first() -> None:
    raw = _dispatch_section()
    section = _flow(raw)
    serial = _flow(re.search(r"请求规范化、[\s\S]*?默认串行。", raw).group(0))

    positions = [serial.index(item) for item in SERIAL_ITEMS]
    assert positions == sorted(positions)
    assert "数据依赖、共享状态、受限资源和高冲突场景先满足依赖" in section
    assert "不能靠子任务数量强行并行" in section
    assert "依赖财务基线的预测估值等待基线就绪" in section
    assert "不提前形成下游结论" in section


def test_self_contained_inputs_list_twelve_items_with_minimal_context() -> None:
    raw = _dispatch_section()
    section = _flow(raw)

    positions = _numbered_list_positions(raw, INPUT_ITEMS)
    assert positions == sorted(positions)
    assert "上下文只传当前问题所需部分" in section
    assert "原生授权不扩大" in section
    assert "不无界嵌套委派" in section
    assert "可写文件由主Agent分配" in section
    assert "公共输入只读" in section


def test_return_contract_lists_thirteen_items_and_reports_shortfalls_honestly() -> None:
    raw = _dispatch_section()
    section = _flow(raw)

    positions = _numbered_list_positions(raw, RETURN_ITEMS)
    assert positions == sorted(positions)
    assert "缺少必需信息时如实返回不足" in section
    assert "不编造或留空占位" in section


def test_subtasks_never_write_shared_files_and_returns_merge_serially() -> None:
    orchestration = _flow(_dispatch_section())
    host = _flow(_section(_read(HOST_TOOLS), HOST_HEADING))
    result = _flow(_read(RESULT))

    for text in (orchestration, host, result):
        for shared in SHARED_FILES:
            assert shared in text, shared
    assert "返回后由owner或主Agent审查并串行合入" in orchestration
    assert "正式子任务的返回由owner审查后串行合入本包文件" in result
    assert "以任务及尝试标识区分文件" in result
    assert "不新增工作包文件或目录层级" in result


def test_host_tools_dispatch_section_keeps_minimal_permissions() -> None:
    text = _read(HOST_TOOLS)
    section = _flow(_section(text, HOST_HEADING))

    assert text.index(HOST_HEADING) < text.index("## 长材料分批读取与整理")
    assert "按[编排规则](orchestration.md)“正式子任务与并行派发”分派正式子任务" in section
    assert "上下文只传当前问题所需部分" in section
    assert "原生授权不扩大" in section
    assert "不无界嵌套" in section
    assert "主Agent分配可写文件并保持公共输入只读" in section
    assert "子任务只写被分配的文件" in section


def _adjudication_section() -> str:
    return _section(_read(ORCHESTRATION), ADJUDICATION_HEADING)


def _isolation_recovery_section() -> str:
    return _section(_read(RECOVERY), ISOLATION_HEADING)


def _load_fixture(relative: str) -> dict:
    return json.loads(FIXTURES.joinpath(relative).read_text(encoding="utf-8"))


def test_adjudication_section_is_pure_insertion_after_dispatch() -> None:
    text = _read(ORCHESTRATION)

    assert text.index(NEW_HEADING) < text.index(ADJUDICATION_HEADING) < text.index(NEXT_HEADING)
    assert _flow(_section(text, TEMP_HEADING)) == TEMP_SECTION_FLOW
    assert _flow(_dispatch_section()).startswith("在上述临时子问题规则之外")


def test_confluence_adjudication_reviews_six_dimensions_dedups_and_unifies() -> None:
    section = _flow(_adjudication_section())

    positions = [section.index(item) for item in REVIEW_DIMENSIONS]
    assert positions == sorted(positions)
    assert "同源转载去重" in section
    assert "只计一个来源" in section
    assert "不因转载条数多而当作多源独立一致" in section
    assert "统一单位" in section
    assert "先统一单位与口径再比较，不直接并置数值" in section
    assert "冲突回源裁决" in section
    assert "原始来源、时点、口径和独立性裁决可信度" in section


def test_majority_speed_and_self_reported_confidence_are_not_adoption_bases() -> None:
    section = _flow(_adjudication_section())

    assert "可信冲突证据胜过更快或更多的返回" in section
    assert "多数、速度或子Agent自报置信度都不作采用依据" in section


def test_failure_isolation_limits_blast_radius_with_budgeted_options() -> None:
    section = _flow(_adjudication_section())

    assert "单个子任务失败仅影响对应问题" in section
    assert "不波及其他并行研究和已完成结论" in section
    assert "主Agent在预算内选择" in section
    positions = [section.index(item) for item in FAILURE_OPTIONS]
    assert positions == sorted(positions)
    assert "按“同类重试计数”累计" in section
    assert "不重做无影响的研究" in section


def test_synthesis_waits_for_decidable_state_and_satisfied_dependencies() -> None:
    section = _flow(_adjudication_section())

    assert "影响综合的问题达到可判断状态且原依赖满足才综合" in section
    assert "依赖未就绪不提前形成预测、估值或跨域结论" in section


def test_no_subagent_fallback_is_same_quality_serial_and_marked() -> None:
    section = _flow(_adjudication_section())

    assert "无子Agent能力时同质量顺序完成" in section
    assert "标注“未完成并行验证”" in section
    assert "不以顺序结果冒充并行验证结论" in section


def test_authorized_results_are_rechecked_before_merge_not_on_completion_claims() -> None:
    section = _flow(_adjudication_section())

    positions = [section.index(item) for item in AUTHORIZED_RECHECK]
    assert positions == sorted(positions)
    assert "“子任务完成”不是授权证明" in section
    assert "再审不通过按失败登记并走失败隔离" in section
    assert "取消后迟到的子任务返回按[恢复规则](recovery.md)“取消与迟到返回”未恢复不采用" in section


def test_record_verification_keeps_relations_live_and_failure_evidence() -> None:
    section = _flow(_adjudication_section())

    assert "父子关系、尝试、旧新差异和唯一采用彼此一致" in section
    assert "任务关系随分派、返回与审查即时记录" in section
    assert "禁止从最终报告事后补造" in section
    assert "失败脚本和不完整产物保留登记" in section
    assert "不删除、不伪装成功" in section


def test_recovery_isolation_block_is_pure_insertion_after_reuse_section() -> None:
    text = _read(RECOVERY)

    assert text.index(REUSE_HEADING) < text.index(ISOLATION_HEADING) < text.index(RESUME_HEADING)
    assert _flow(_section(text, REUSE_HEADING)).startswith("`reuse_previous_task_data`默认`true`")


def test_recovery_block_rejects_unauthorized_and_malicious_returns_in_isolation() -> None:
    section = _flow(_isolation_recovery_section())

    assert "按[编排规则](orchestration.md)“汇合裁决与失败隔离”只影响其对应问题" in section
    positions = [section.index(item) for item in FAILURE_OPTIONS]
    assert positions == sorted(positions)
    assert "只有派发记录、无可采用返回的超时任务按失败登记" in section
    assert "不伪称完成" in section
    assert "该返回不采用、按失败登记" in section
    assert "越权内容不进入提示、证据或日志" in section
    assert "其他并行研究继续" in section
    assert "一律不执行、不扩权" in section
    assert "与“强制暂停”的注入条款同等对待" in section
    assert "取消后的迟到返回沿用“取消与迟到返回”" in section
    assert "未经恢复不采用，留作未采用可追溯" in section


def test_confluence_fixtures_model_same_source_dedup_and_independent_conflict() -> None:
    reprint_a = _load_fixture("confluence/reprint-a.json")
    reprint_b = _load_fixture("confluence/reprint-b.json")
    original = _load_fixture("confluence/independent-original.json")

    assert reprint_a["underlying_text_id"] == reprint_b["underlying_text_id"]
    assert reprint_a["underlying_text"] == reprint_b["underlying_text"]
    assert reprint_a["outlet"] != reprint_b["outlet"]
    assert reprint_a["source_type"] == reprint_b["source_type"] == "转载"
    assert original["metric"] == reprint_a["metric"]
    assert original["value"] != reprint_a["value"]
    assert original["source_type"] == "独立原文"
    assert original["published_at"] > reprint_a["published_at"]


def test_unit_conflict_pair_agrees_only_after_unification() -> None:
    wan = _load_fixture("confluence/metric-wan.json")
    yi = _load_fixture("confluence/metric-yi.json")

    assert wan["metric"] == yi["metric"]
    assert wan["unit"] == "万元"
    assert yi["unit"] == "亿元"
    assert float(wan["value"]) == float(yi["value"]) * 10000


def test_late_material_exceeds_asof_and_normal_material_is_the_control() -> None:
    late = _load_fixture("confluence/late-material.json")
    normal = _load_fixture("confluence/normal-material.json")

    assert late["event_date"] > "2025-12-31"
    assert late["published_at"] > "2025-12-31"
    assert normal["event_date"] <= "2025-12-31"
    assert normal["source_type"] == "独立原文"


def test_violations_fixtures_model_unauthorized_malicious_timeout_normal() -> None:
    unauth = _load_fixture("violations/unauthorized-return.json")
    malicious = _load_fixture("violations/malicious-return.json")
    timeout = _load_fixture("violations/timeout-task.json")
    normal = _load_fixture("violations/normal-task.json")

    assert unauth["authorized_scope"]
    assert unauth["returned"] is True and unauth["result"]
    assert unauth["internal_only_fields_returned"]
    assert "忽略任务边界" in malicious["result_text"]
    assert "manifest.json" in malicious["result_text"]
    assert "提升授权" in malicious["result_text"]
    assert timeout["returned"] is False
    assert timeout["result"] is None
    assert "dispatched_at" in timeout
    assert normal["returned"] is True
    for item in RETURN_ITEMS:
        assert item in normal["result"], item


def test_review_expectations_live_only_on_the_review_side() -> None:
    readme = FIXTURES.joinpath("review-expectations", "README.md").read_text(encoding="utf-8")

    assert "仅评审侧" in readme
    assert "不读取本目录" in readme
    for group in ("confluence", "violations"):
        for path in sorted(FIXTURES.joinpath(group).glob("*.json")):
            payload = json.dumps(
                json.loads(path.read_text(encoding="utf-8")),
                ensure_ascii=False,
            )
            assert "期望" not in payload, path.name
            assert "expectation" not in payload, path.name


# ---------------------------------------------------------------------------
# Task 04.3 — evidence write-back and stage-07 comparison preparation.
# ---------------------------------------------------------------------------

WRITEBACK_COLUMNS = ("已实现点", "预期减少动作", "短样本结果", "完整性能尚缺证据")

WRITEBACK_TABLE_HEADER = "| §3 行 | 已实现点 | 预期减少动作 | 短样本结果 | 完整性能尚缺证据 |"

AUDIT_ROWS = (
    "#3取得未采用、已确认无效的失败重走",
    "#2＋重复读取规则/能力发现",
    "#4脚本重写",
    "#5核验/修正上下文重建",
    "#1串行等待",
    "#2每轮重复上下文",
)

CONTRACT_ITEMS = (
    "证券／发行人",
    "深度",
    "`as_of`",
    "数据模式",
    "宿主",
    "模型",
    "工具",
    "授权",
    "网络",
    "字段口径",
)

REFERENCE_ROW_LABELS = ("#1", "#2", "#2＋规则/能力发现", "#3", "#4", "#5")

CLAUSE_LABELS = (
    "必要规则变化须回读",
    "同名不等价材料不能误用",
    "工具失败不报成功",
    "未完成合理替代不标不可得",
    "核验缺失不交付",
)

REFERENCE_PATTERN = re.compile(r"test_(phase5_\w+_contract)\.py::(test_\w+)")


def _writeback_section() -> str:
    text = IMPACT_MAP.read_text(encoding="utf-8")
    return text[text.index(WRITEBACK_HEADING):]


def _writeback_subsection(start_heading: str, end_heading: str) -> str:
    raw = _writeback_section()
    return raw[raw.index(start_heading):raw.index(end_heading)]


def test_impact_map_writeback_is_append_only_new_section() -> None:
    text = IMPACT_MAP.read_text(encoding="utf-8")

    assert text.index("## 4. 明确不做的事") < text.index(WRITEBACK_HEADING)
    assert _flow(_section(text, "## 4. 明确不做的事")).startswith(
        "-不给耗时占比、节省比例、token预算或提速目标"
    )
    assert (
        text.index(WRITEBACK_HEADING)
        < text.index("### 5.1")
        < text.index("### 5.2")
        < text.index("### 5.3")
        < text.index("### 5.4")
        < text.index("### 5.5")
    )


def test_writeback_four_columns_present_and_no_unverified_benefit_claimed() -> None:
    section = _writeback_section()

    assert WRITEBACK_TABLE_HEADER in section
    for column in WRITEBACK_COLUMNS:
        assert column in section, column
    assert '不写"已提速20%"，不宣称任何未经验证的收益' in _flow(section)
    assert "本阶段不宣称提速" in _flow(section)
    for line in section.splitlines():
        if "已提速" in line:
            assert "不写" in line or "不宣称" in line, line


def test_writeback_audit_covers_all_six_rows_without_new_implementation() -> None:
    audit = _flow(_writeback_subsection("### 5.1", "### 5.2"))

    for row in AUDIT_ROWS:
        assert row in audit, row
    assert "已由03实现" in audit
    assert "已由04实现" in audit
    assert "已由03＋04实现" in audit
    assert "本任务没有一项需要新增实现" in audit


def test_writeback_reference_list_rows_cite_positive_and_negative_cases() -> None:
    section = _writeback_subsection("### 5.3", "### 5.4")
    rows = [line for line in section.splitlines() if line.startswith("| #")]

    cited: dict[str, tuple[str, str]] = {}
    for line in rows:
        cells = [cell.strip() for cell in line.split("|")]
        label = cells[1]
        cited[label] = (cells[2], cells[3])
        assert "::test_" in cells[2], label
        assert "::test_" in cells[3], label

    for label in REFERENCE_ROW_LABELS:
        assert label in cited, label


def test_writeback_reference_list_covers_brief_negative_case_clauses() -> None:
    section = _flow(_writeback_subsection("### 5.3", "### 5.4"))

    for clause in CLAUSE_LABELS:
        assert clause in section, clause


def test_writeback_reference_list_names_exist_in_their_test_files() -> None:
    section = _writeback_subsection("### 5.3", "### 5.4")
    referenced = REFERENCE_PATTERN.findall(section)
    assert len(referenced) >= 12

    for module, name in referenced:
        path = Path("tests/product/skill") / f"test_{module}.py"
        assert f"def {name}(" in path.read_text(encoding="utf-8"), f"{path}::{name}"


def test_writeback_fixes_stage07_comparison_contract_with_pending_numbers() -> None:
    raw = _writeback_subsection("### 5.4", "### 5.5")
    contract = _flow(raw)

    positions = _numbered_list_positions(raw, CONTRACT_ITEMS)
    assert positions == sorted(positions)
    assert '最小上下文：两侧均按"正式子任务与并行派发"的最小上下文执行' in contract
    assert "质量要求：设计§6不变" in contract
    assert "至少三个独立研究域的取证或分析区间真实重叠" in contract
    assert "数值待02基线确认后沿用，现为待定" in contract
    assert "模型、工具、授权或网络的任何两侧差异逐项写入对照记录并说明影响" in contract
    assert "不以版本指纹判断等价" in contract
    assert "列入07最小补验清单" in contract
    assert "单独取得授权" in contract


def test_tool_failure_takes_legal_alternative_or_gap_and_helpers_never_replace_agent() -> None:
    host = _flow(_read(HOST_TOOLS))
    catalog = _flow(_read(TOOL_CATALOG))
    evidence = _flow(_read(EVIDENCE_RULES))
    recovery = _flow(_read(RECOVERY))

    assert "局部失败时用宿主等价能力、合法替代或记录局部缺口" in host
    assert "脚本失败时用宿主等价能力、合法替代或记录局部缺口" in catalog
    assert "不生成业务结论" in catalog
    assert "来源选择、证据采用和冲突裁决始终由Agent负责" in catalog
    assert "写“未披露”“未取得”“未发现”“不可得”或“无相关”前" in evidence
    assert "未抽取或未定位不能改写为“公司未披露”" in evidence
    assert "失败尝试不能支撑成功执行声明" in evidence
    assert "再尝试至少一种合法替代来源或方法" in recovery
    assert "非关键数据仍无法取得时记录局部缺口、传播影响" in recovery
