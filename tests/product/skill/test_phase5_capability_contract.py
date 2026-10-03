"""Stage-03 task 03.3 static prompt-contract tests.

Assert the text invariants of the capability-precheck, independent-verification
entry, byte-budget batching, compression hand-off and read-reduction contract
added to the canonical Skill references by phase-5 task 03.3.
The versioned long-material behavior sample is exercised separately by the
controller.
"""

import re
from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")
SKILL = "SKILL.md"
HOST_TOOLS = "references/host-tools.md"
CHECKPOINT = "references/checkpoint.md"

PRECHECK_ITEMS = (
    "宿主版本与实际模型",
    "上下文容量",
    "网页与数据访问",
    "PDF",
    "原子工具",
    "授权边界",
    "子Agent派发能力",
)

SPLIT_DIMENSIONS = (
    "同名不同主体",
    "点时前后版本",
    "单位变化",
    "关键否定",
    "冲突",
    "授权限制",
    "用户纠正",
)

HANDOFF_INVARIANTS = (
    "证券与主体",
    "时点",
    "数据模式",
    "授权范围",
    "来源与原文定位",
    "计算输入与口径",
    "关键事实",
    "冲突",
    "缺口",
    "失效记录",
    "用户决定",
)

PROVEN_DUPLICATES = ("规则重复读取", "同问题重复检索", "长材料反复解析")

CHECKPOINT_MOMENTS = (
    "建立候选任务卡",
    "形成正式任务卡",
    "宿主能力变化",
    "工作包状态变化",
    "取得关键证据",
    "出现冲突或缺口",
    "采用替代",
    "获得用户决定",
    "触发回访",
    "暂停/恢复",
    "进入W10及每轮自检后更新",
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


def test_capability_precheck_covers_inventory_and_unknown_versions() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 研究前能力预检"))

    assert "开始研究前按请求深度" in section
    assert "记入`checkpoint.md`的宿主能力条目" in section
    for item in PRECHECK_ITEMS:
        assert item in section, item
    assert "只记录实际可验证的能力" in section
    assert "不猜测版本号或模型名" in section
    assert "正式支持认证" in section
    assert "不替代任何宿主或宿主组合的" in section


def test_missing_required_capability_degrades_waits_or_stops_by_existing_rules() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 研究前能力预检"))

    assert "缺少请求深度所需能力时说明影响" in section
    assert "按既有边界降级并披露实际深度" in section
    assert "等待合法能力就绪" in section
    assert "“能力不足”停止条件停止对应工作" in section
    assert "不伪装完成" in section


def test_independent_verification_entry_is_fixed_with_nested_fallback() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 独立核对入口"))

    assert "已验证可用的独立上下文入口" in section
    assert "不每份报告重新探索CLI或临时入口" in section
    assert "记录实际能力与限制" in section
    assert "后续核对直接复用该入口" in section
    assert "嵌套环境缺少子Agent派发能力时" in section
    assert "使用已验证的协调层建立核对上下文" in section
    assert "不在每份报告内重复摸索" in section
    assert "明确独立核对未完成" in section
    assert "不假装已核对" in section


def test_first_verification_complete_and_fixups_recheck_direct_impact_only() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 独立核对入口"))

    assert "首次独立核对始终完整执行" in section
    assert "修正后核对者只复查修正项及其直接影响范围" in section
    assert "不改成原上下文自核" in section
    assert "不因修正重复整篇核对" in section


def test_byte_budget_is_uncalibrated_reference_with_per_batch_continuation() -> None:
    raw = _section(_read(HOST_TOOLS), "## 长材料分批读取与整理")
    section = _flow(raw)

    assert "参考值（历史假设，未验证，待校准）" in section
    assert "不得自动升级为批准上限" in section
    assert "不与性能目标混用" in section
    assert "50,000" in section
    for line in raw.splitlines():
        if "50,000" in line:
            assert "参考值" in line, line
            assert "待校准" in line, line
    assert "把本轮实际采用的单批预算随宿主能力记入`checkpoint.md`" in section
    assert "每批读取后立即按当前问题继续抽取、核对和记录" in section
    assert "不等全部批次读完才开始" in section


def test_batching_does_not_truncate_must_read_rules_or_skip_full_documents() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert "分批不截断必读规则、例外、否定和条件" in section
    assert "仍须分批读完" in section
    assert "不只读摘要省时" in section
    assert "摘要用于定位，必要核验回到原文" in section


def test_consolidation_triggers_preserve_checkpoint_update_timing() -> None:
    host_section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert "保留[检查点规则](checkpoint.md)的全部既定更新时机" in host_section
    assert "按材料规模（已读取批次与字节量）" in host_section
    assert "宿主实际暴露的信号（上下文余量、压缩或compact提示）" in host_section
    assert "额外整理供恢复使用的检查点记录" in host_section
    assert "宿主原生压缩与恢复" in host_section
    assert "不新增压缩引擎或研究状态机" in host_section

    timing = _flow(_section(_read(CHECKPOINT), "## 更新时机"))
    for moment in CHECKPOINT_MOMENTS:
        assert moment in timing, moment
    assert "不得为缩短文件删除原失败、冲突、用户决定、失效结论或来源定位" in timing
    assert "上述时机全部保持不变" in timing
    assert "另按材料规模或宿主压缩、上下文余量信号额外整理供恢复使用的记录" in timing
    assert "不缩短、不替代上述时机" in timing


def test_capacity_visible_and_unknown_take_different_routes() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert "宿主实际暴露上下文余量或压缩信号时按实际信号安排整理" in section
    assert "未暴露时按已验证的读取批次推进" in section
    assert "不猜测剩余百分比" in section


def test_versioned_material_dimensions_survive_batches_and_compression() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert (
        "同名不同主体、点时前后版本、单位变化、关键否定、冲突、授权限制和用户纠正"
        "可能分散在不同文件或读取批次" in section
    )
    for dimension in SPLIT_DIMENSIONS:
        assert dimension in section, dimension
    assert "完整保留并相互区分" in section
    assert "不因分批、整理或压缩丢失或混同" in section
    assert "回答与转交以原文为据" in section


def test_compression_handoff_preserves_invariants_and_rereads_source() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert "压缩或转交后必须仍然保留并与当前目标、预算和采用记录一致" in section
    for invariant in HANDOFF_INVARIANTS:
        assert invariant in section, invariant
    assert "只保存结论依据与定位" in section
    assert "不保存隐藏思维链" in section
    assert "发现摘要不足以支持当前决定时，先回读原文再继续" in section


def test_per_host_safe_read_amounts_registered_and_unverified_not_claimed() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 长材料分批读取与整理"))

    assert "经长语料验证的单批安全读取量及适用证据" in section
    assert "登记在本文件" in section
    assert "未验证的宿主与材料组合不声明支持长上下文验收" in section
    assert "按参考值保守执行并记录缺口" in section


def test_read_reduction_limited_to_proven_duplicates_with_required_coverage() -> None:
    section = _flow(_section(_read(HOST_TOOLS), "## 重复读取的有限减少"))

    assert "只对02基线已证明重复的三类读取实施减少" in section
    for duplicate in PROVEN_DUPLICATES:
        assert duplicate in section, duplicate
    assert "不因再次提问整份重读" in section
    assert "同一问题已覆盖的检索不重复发起" in section
    assert "不因再次提问反复整份重新解析" in section
    assert "必读规则及其例外的完整覆盖" in section
    assert "独立核对者自行回源" in section
    assert "回访重开后的重读" in section
    assert "原文回读" in section
    assert "必需项的实际覆盖" in section
    assert "不得按“读过、可用”继续" in section
    assert "详细规则仍经正文导航直接可发现" in section


def test_skill_body_stays_short_and_resources_directly_discoverable() -> None:
    text = _read(SKILL)
    lines = text.splitlines()

    assert len(lines) <= 500
    assert len(text.split()) <= 5000
    assert "## 按需资源导航" in text
    assert "](references/host-tools.md)" in text
    assert "发现宿主能力、选择文件位置、调用可选助手或开始读取年报等长材料时读取" in text
