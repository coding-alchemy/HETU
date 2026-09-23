"""Phase-5 middle static prompt-contract tests (reuse and provenance).

Assert the text invariants of the default-on reuse switch, applicability
check, independent local copies, incremental refetch and false-mode
no-fallback contract migrated to the canonical Skill references for the
phase-5 middle scope (M2). Each R judgment point must have a supporting
clause here.

Evidence distinction (kept accurate): R01/R02 were real runs with actual
product trees; the original R03 was a contract-judgment record with
example values, not a run tree — its "allowed copy scope" and "failed
copy not registered as success" behaviors were later backed by real
execution evidence in B1 section 3 and R04 (offline reuse verification).
"""

import re
from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")
SKILL = "SKILL.md"
RECOVERY = "references/recovery.md"
ARTIFACTS = "references/artifact-contract.md"
HOST_TOOLS = "references/host-tools.md"
W0 = "references/work-packages/core/W0-task-framing.md"
W10 = "references/work-packages/core/W10-report-review.md"

REUSE_HEADING = "## 旧任务资料复用与独立副本"
FILTER_HEADING = "## 恢复候选筛选"
CONTEXT_HEADING = "## 关闭复用的上下文边界"
PRECHECK_HEADING = "## 研究前能力预检"
INDEPENDENT_REVIEW_HEADING = "## 定稿前独立证据核对"

APPLICABILITY_DIMENSIONS = ("主体", "期间", "口径", "点时", "授权", "输入方法")

PROVENANCE_KEYS = (
    "source_task_id",
    "source_artifact",
    "original_source",
    "original_acquired_at",
    "copied_at",
)

PROVENANCE_DEFINITION_SNIPPETS = (
    "条目材料复制自来源任务时记录，五子键齐全且均为非空字符串",
    "两个时间字段为带时区ISO8601",
    "值不含秘密",
    "`source_artifact`为来源任务内可核对的相对定位",
    "不得为绝对路径或含`..`的逃逸路径",
    "仅结构校验，不访问来源任务目录，不要求来源文件仍在本地",
    "`inputs`仍只引用本manifest的本地副本，被复制文件本体登记为本次产物",
    "显式矛盾即失败：任何条目带`provenance`而`run.reuse_previous_task_data=false`",
)


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


def _reuse_section() -> str:
    return _flow(_section(_read(RECOVERY), REUSE_HEADING))


def test_reuse_switch_defaults_true_and_nl_false_mapping_is_unified() -> None:
    w0 = _flow(_read(W0))
    section = _reuse_section()

    assert "该开关默认`true`" in w0
    assert "`reuse_previous_task_data`默认`true`" in section
    for text in (w0, section):
        assert "不用旧任务数据" in text
        assert "从头分析" in text
        assert "映射为`false`" in text
    assert "该开关默认`true`；用户说“不用旧任务数据、从头分析”之类自然语言时映射为`false`" in w0
    assert "用户说“不用旧任务数据，从头分析”之类自然语言时映射为`false`" in section


def test_reuse_reuses_existing_filtering_without_new_unified_index() -> None:
    section = _reuse_section()

    assert "旧任务候选查找沿用恢复候选筛选的目录与检查点筛选" in section
    assert "不新增统一历史索引" in section
    assert "`reuse_previous_task_data`默认`true`，只控制旧任务资料与成果的使用" in section


def test_reuse_true_checks_applicability_before_copying() -> None:
    section = _reuse_section()

    assert "旧年报及解析结果" in section
    assert "核对主体、期间、口径、点时、授权和输入方法" in section
    for dimension in APPLICABILITY_DIMENSIONS:
        assert dimension in section, dimension
    assert "主体、期间或口径不符时不采用、不误用" in section


def test_local_copy_invariants_not_link_not_wholesale_not_disguised() -> None:
    section = _reuse_section()
    artifacts = _flow(_read(ARTIFACTS))

    assert "把本次实际采用的文件和必要输入复制进当前研究根" in section
    assert "读取、核验及报告引用使用本地副本" in section
    assert "不是依赖旧目录的链接" in section
    assert "不整套复制旧历史" in section
    assert "不把复制的旧文件伪装成本次新下载" in section

    assert "只复制本次实际采用的文件及其必要输入" in artifacts
    assert "均使用本地副本，不把依赖旧目录的链接当作独立副本" in artifacts
    assert "不整套复制来源任务的无关历史" in artifacts
    assert "复制的旧文件不得登记为本次新下载" in artifacts


def test_incremental_refetch_no_full_redownload_to_prove_reuse() -> None:
    section = _reuse_section()

    assert "新披露、更正、缺失或损坏只补取受影响的相关资料" in section
    assert "不为了证明复用而先完整重下载" in section


def test_unlicensed_copy_failure_and_old_directory_unchanged() -> None:
    section = _reuse_section()
    artifacts = _flow(_read(ARTIFACTS))

    assert "许可不允许复制的材料只保留允许的定位与限制说明" in section
    assert "不越权复制" in section
    assert "复制中断或失败时不登记“独立保存成功”" in section
    assert "旧目录与原获取时间保持不变" in section
    assert "许可不允许复制时只保留允许的定位与限制说明，不登记为本地产物" in artifacts


def test_r01_default_copies_old_material_and_stays_readable_when_isolated() -> None:
    """R01 (real run with product tree): old annual report and parsing
    exist, interim report is missing — default copies only the old
    material and fetches the missing one; with the old directory
    isolated, this run's copies and necessary inputs stay readable, and
    report references close on locally registered artifacts."""
    section = _reuse_section()
    artifacts = _flow(_read(ARTIFACTS))

    assert "新披露、更正、缺失或损坏只补取受影响的相关资料" in section
    assert "旧目录不可访问时本次副本及必要输入仍可读取核对" in section
    assert "旧目录与原获取时间保持不变" in section
    assert "读取、核验及报告引用使用本地副本" in section
    assert "被复制文件本体登记为本次产物" in artifacts
    assert "`inputs`仍只引用本manifest的本地副本" in artifacts


def test_r02_reuse_false_fetches_all_and_never_falls_back() -> None:
    """R02 (real run): same input with explicit false — everything needed
    is fetched this run; a failed new source leaves a legal alternative
    or a recorded gap, never a fallback read of old material."""
    section = _reuse_section()
    filters = _flow(_section(_read(RECOVERY), FILTER_HEADING))

    assert "本次所需资料全部按当前请求获取" in section
    assert "不查找也不载入任何旧任务资料" in section
    assert "寻找合法替代来源或记录缺口" in section
    assert "不回退读取旧资料补数" in section
    assert "`reuse_previous_task_data=false`" in filters
    assert "不查找也不载入任何旧任务资料" in filters


def test_r03_conflict_license_interruption_and_missing_inputs() -> None:
    """R03 was a contract-judgment record with example values (not a run
    tree); its four sub-scenarios gained real execution evidence in
    B1 section 3 and R04. Clauses: subject/period conflict, license
    limits, interrupted copy and derived results without their original
    inputs must not be misused, must not overreach the license, and must
    not be registered as saved."""
    section = _reuse_section()

    assert "主体、期间或口径不符时不采用、不误用" in section
    assert "只保留允许的定位与限制说明，不越权复制" in section
    assert "复制中断或失败时不登记“独立保存成功”" in section
    assert "复用解析或计算结果时一并复制必要的合法原始输入与依赖" in section
    assert "缺失时按缺口处理" in section


def test_provenance_five_key_definition_unchanged() -> None:
    text = _read(ARTIFACTS)
    flow = _flow(text)

    for key in PROVENANCE_KEYS:
        assert f"`{key}`" in text
    for snippet in PROVENANCE_DEFINITION_SNIPPETS:
        assert snippet in flow, snippet
    run_field_clause = (
        "`reuse_previous_task_data`（布尔，实际复用开关）。出现即校验类型与格式；不出现不判失败"
    )
    assert run_field_clause in flow


# --- R1: reuse-off context boundary rules (M2-5/M2-6 run rules) ---


def _context_section() -> str:
    return _flow(_section(_read(RECOVERY), CONTEXT_HEADING))


def test_reuse_false_bans_old_materials_and_in_context_old_conclusions() -> None:
    """reuse=false forbids not only looking up/loading old task materials
    but also adopting old-task conclusions already present in the current
    input context — 'not re-reading the file' is no excuse."""
    section = _context_section()

    assert "禁止读取或采用旧任务的原始／派生材料、报告、研究脚本、检查点及研究上下文" in section
    assert "已在本次输入上下文中的旧任务结论也不得采用" in section
    assert "没有重新读取旧文件" in section


def test_reuse_false_checks_actual_context_before_claiming_from_scratch() -> None:
    """Before declaring from-scratch research the author checks the actual
    input context, host-observable automatic history loading and tool
    access scope; a false field, a fresh directory or a new session alone
    is not isolation proof; unobservable parts stay recorded gaps."""
    section = _context_section()

    assert "核对本次实际输入上下文" in section
    assert "宿主可观察的自动历史加载情况" in section
    assert "工具访问边界" in section
    assert "`false`字段、新研究目录或新会话本身都不构成完整隔离证明" in section
    assert "不宣称完整隔离通过" in section
    assert "不伪造对不可观察内容的检查" in section
    assert "能力／证据缺口" in section


def test_reuse_false_boundary_passed_to_subtasks_and_independent_review() -> None:
    """The actual switch and its restrictions reach ad-hoc research
    subtasks and the pre-delivery independent review; a subtask's
    'independent context' is not automatically old-material-free."""
    section = _context_section()

    assert "实际开关与对应禁用边界必须传递给参与本次研究的临时研究子任务和交付前独立核验" in section
    assert "只要求主任务遵守不算完成传递" in section
    assert "不自动视为无旧资料" in section

    w10 = _flow(_section(_read(W10), INDEPENDENT_REVIEW_HEADING))
    assert "`reuse_previous_task_data=false`" in w10
    assert "把实际开关与对应禁用边界一并传递给核对者" in w10
    assert "不得查找、读取或采用旧任务资料" in w10

    precheck = _flow(_section(_read(HOST_TOOLS), PRECHECK_HEADING))
    assert "`reuse_previous_task_data=false`" in precheck
    assert "自动历史加载" in precheck
    assert "能力／证据缺口" in precheck


def test_reuse_false_boundary_preserves_existing_scopes() -> None:
    """The boundary must not be confused with same-task recovery and must
    not remove existing permissions: current-task recovery keeps its own
    records; canonical tools, the Skill, the installed environment and
    valid authorizations stay usable; no old-task deletion, no global
    history wipe, no new scheduling or isolation framework."""
    section = _context_section()

    assert "恢复当前任务仍可使用本任务自身已记录的成果" in section
    assert "正式工具、Skill、已安装环境和有效授权仍可正常使用" in section
    assert "不删除旧任务" in section
    assert "不全局清除历史" in section
    assert "不引入正式并行调度或新的隔离系统" in section


# --- R2: normal-entry loading of the reuse rules ---


def test_normal_entry_loads_reuse_rules_before_old_material_decisions() -> None:
    """SKILL.md navigation triggers reading the reuse rules on the normal
    path: after the switch is determined and before deciding old-material
    usage and the fetch strategy; both default-true and explicit-false
    reach their rules without waiting for a tool failure or a recovery
    problem; reading the rules is not itself old-material access."""
    skill = _read(SKILL)
    flow = _flow(skill)

    assert (
        "确定复用开关后、决定旧任务材料使用与对应获取策略前，"
        "读取[恢复规则](references/recovery.md)的复用细则" in flow
    )
    assert "默认`true`与显式`false`均须先读取对应规则" in flow
    assert "不依赖工具失败或恢复问题触发" in flow
    assert "读取规则本身不构成旧资料访问" in flow
    # 失败／恢复触发入口保留，不被新入口替代。
    assert "工具、来源、授权或恢复出现问题时读取[恢复规则](references/recovery.md)" in flow
    # 按需加载方式不变：启动工作集仍不含恢复规则或全部 references。
    step1 = skill.split("1. 读取", 1)[1].split("2. ", 1)[0]
    assert "recovery" not in step1
    assert "建立默认启动工作集" in step1


def test_w0_reads_reuse_rules_after_switch_before_old_material_access() -> None:
    """W0 records the switch first, then reads and applies the reuse
    rules before any old-material access or fetch decision; the false
    path additionally points at the context-boundary rules."""
    w0 = _flow(_read(W0))

    assert (
        "确定开关后、决定旧任务材料使用与对应获取策略前，"
        "读取并执行[恢复规则](../../recovery.md)的复用细则" in w0
    )
    assert "在访问任何旧任务资料之前" in w0
    assert "读取规则本身不构成旧资料访问" in w0
    assert "「关闭复用的上下文边界」" in w0
