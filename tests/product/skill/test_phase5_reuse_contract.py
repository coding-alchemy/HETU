"""Stage-03 task 03.4 static prompt-contract tests.

Assert the text invariants of the default-on reuse switch, applicability
check, independent local copies, incremental refetch and false-mode
no-fallback contract added to the canonical Skill references by phase-5
task 03.4. Real-run comparisons R01-R03 are exercised separately by the
controller; each R judgment point must have a supporting clause here.
"""

import re
from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")
RECOVERY = "references/recovery.md"
ARTIFACTS = "references/artifact-contract.md"
W0 = "references/work-packages/core/W0-task-framing.md"

REUSE_HEADING = "## 旧任务资料复用与独立副本"
FILTER_HEADING = "## 恢复候选筛选"

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
    """R01: old annual report and parsing exist, interim report is missing —
    default copies only the old material and fetches the missing one; with the
    old directory isolated, this run's copies and necessary inputs stay
    readable, and report references close on locally registered artifacts."""
    section = _reuse_section()
    artifacts = _flow(_read(ARTIFACTS))

    assert "新披露、更正、缺失或损坏只补取受影响的相关资料" in section
    assert "旧目录不可访问时本次副本及必要输入仍可读取核对" in section
    assert "旧目录与原获取时间保持不变" in section
    assert "读取、核验及报告引用使用本地副本" in section
    assert "被复制文件本体登记为本次产物" in artifacts
    assert "`inputs`仍只引用本manifest的本地副本" in artifacts


def test_r02_reuse_false_fetches_all_and_never_falls_back() -> None:
    """R02: same input with explicit false — everything needed is fetched this
    run; a failed new source leaves a legal alternative or a recorded gap,
    never a fallback read of old material."""
    section = _reuse_section()
    filters = _flow(_section(_read(RECOVERY), FILTER_HEADING))

    assert "本次所需资料全部按当前请求获取" in section
    assert "不查找也不载入任何旧任务资料" in section
    assert "寻找合法替代来源或记录缺口" in section
    assert "不回退读取旧资料补数" in section
    assert "`reuse_previous_task_data=false`" in filters
    assert "不查找也不载入任何旧任务资料" in filters


def test_r03_conflict_license_interruption_and_missing_inputs() -> None:
    """R03: subject/period conflict, license limits, interrupted copy and
    derived results without their original inputs must not be misused,
    must not overreach the license, and must not be registered as saved."""
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
