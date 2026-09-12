"""Stage-03 task 03.2 static prompt-contract tests.

Assert the text invariants of the budget, stop-condition, timeout, cancel and
recovery contract added to the canonical Skill references by phase-5 task 03.2.
Behavior samples (C01-C07) are exercised separately by the controller.
"""

import re
from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")
ORCHESTRATION = "references/orchestration.md"
RECOVERY = "references/recovery.md"
W0 = "references/work-packages/core/W0-task-framing.md"
W10 = "references/work-packages/core/W10-report-review.md"

SEVEN_STOP_KINDS = (
    "深度满足",
    "继续检索价值过低",
    "非时间预算耗尽",
    "取消",
    "能力不足",
    "授权不足",
    "安全阻断",
)

TIMEOUT_FIELDS = (
    "目标值（如有）",
    "实际耗时",
    "卡点",
    "已完成域",
    "剩余工作",
    "关键缺口",
    "预算状态",
    "下一动作",
)

RECOVERY_FILTERS = ("会话关联", "公司", "日期", "深度", "研究目标")


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


def test_budget_values_are_uncalibrated_references_not_approved_defaults() -> None:
    raw = _section(_read(ORCHESTRATION), "## 三档预算")
    section = _flow(raw)

    for header in ("外部调用上限", "同类重试上限", "并行度上限", "授权数据费用边界"):
        assert header in section
    depth_rows = [
        line for line in raw.splitlines() if re.match(r"^\| (quick|standard|deep) \|", line)
    ]
    assert len(depth_rows) == 3
    for row in depth_rows:
        assert "参考值" in row, row
        assert "待校准" in row, row
    assert "参考值（历史假设，未验证，待校准）" in section
    assert "不得自动升级为默认" in section
    for line in raw.splitlines():
        if re.search(r"\b\d{3}\b", line):
            assert "参考值" in line, line
            assert "默认" not in line, line


def test_budget_observability_gap_subscription_boundary_and_near_exhaustion() -> None:
    section = _flow(_section(_read(ORCHESTRATION), "## 三档预算"))

    assert "写缺口" in section
    assert "不填造精确值" in section
    assert "不把已有订阅的授权范围误记为本次新增费用授权" in section
    assert "不把未知费用当作无限额度" in section
    assert "预算即将耗尽时优先处理最影响核心论点的未知" in section
    assert "预算到限不能把未满足的深度标成完成" in section
    assert "W0记录实际采用值" in section


def test_w0_records_budget_and_reuse_before_accessing_old_materials() -> None:
    text = _flow(_read(W0))

    assert "三档预算实际采用值" in text
    assert "在访问任何旧任务资料之前" in text
    assert "实际采用值" in text
    assert "复用" in text
    assert "记录缺口" in text
    assert "不把已有订阅的授权范围误记为本次新增费用授权" in text


def test_same_target_retry_counting_switches_path_or_leaves_gap() -> None:
    section = _flow(_section(_read(ORCHESTRATION), "## 同类重试计数"))

    assert "对象、来源和失败原因" in section
    assert "累计计数" in section
    assert "同类重试到达上限后" in section
    assert "切换合法路径、缩小问题或记录缺口" in section
    assert "不再发起同类失败请求" in section


def test_seven_stop_kinds_each_stop_only_their_own_work() -> None:
    raw = _section(_read(ORCHESTRATION), "## 停止条件")
    section = _flow(raw)

    assert "相互独立" in section
    assert "各自只停止其对应的工作" in section
    positions = []
    for kind in SEVEN_STOP_KINDS:
        match = re.search(rf"^\d+\. {kind}：", raw, re.MULTILINE)
        assert match is not None, kind
        positions.append(match.start())
    assert positions == sorted(positions)
    assert "超时不是停止条件" in section


def test_timeout_continues_work_and_records_progress_template() -> None:
    section = _flow(_section(_read(ORCHESTRATION), "## 超时行为"))

    assert "继续必要研究" in section
    assert "不请求续时" in section
    assert "不暂停" in section
    assert "不降低深度" in section
    for field in TIMEOUT_FIELDS:
        assert field in section, field
    assert "用时" in section and "不造数值" in section
    assert "不编造分钟数或原因" in section


def test_cancel_stops_new_calls_and_late_returns_stay_unadopted() -> None:
    section = _flow(_section(_read(RECOVERY), "## 取消与迟到返回"))

    assert "宿主原生接口" in section
    assert "停止发起新工具调用和新子任务" in section
    assert "安全停止" in section
    assert "迟到返回一律留作未采用" in section
    assert "未经恢复不进入任何owner" in section
    assert "不能认证为支持取消" in section


def test_scope_adjustment_preserves_evidence_and_new_items_stay_open() -> None:
    section = _flow(_section(_read(RECOVERY), "## 中途调整与提前交付"))

    assert "只调整剩余工作" in section
    assert "无冲突既有证据保留" in section
    assert "按未完成处理" in section
    assert "新增必需项与剩余预算" in section
    assert "不只改深度标签" in section
    assert "提前交付" in section
    assert "独立核验与安全要求" in section
    assert "披露实际深度与缺口" in section
    assert "不省略W10两轮自检" in section


def test_w10_keeps_two_round_self_check_for_early_delivery() -> None:
    text = _flow(_read(W10))

    assert "提前交付" in text
    assert "披露实际深度与缺口" in text
    assert "提前交付不省略本包两轮自检" in text


def test_recovery_filters_candidates_before_reading_checkpoints() -> None:
    section = _flow(_section(_read(RECOVERY), "## 恢复候选筛选"))

    for condition in RECOVERY_FILTERS:
        assert condition in section, condition
    assert "筛选候选任务" in section
    assert "按需读取" in section
    assert "不全量读取旧报告或原始资料" in section
    assert "真实歧义" in section
    assert "不索要内部任务ID" in section
    assert "不能猜选或宣称已恢复" in section


def test_reuse_false_new_research_never_loads_old_task_records() -> None:
    section = _flow(_section(_read(RECOVERY), "## 恢复候选筛选"))

    assert "`reuse_previous_task_data=false`" in section
    assert "恢复当前任务时使用本任务已记录" in section
    assert "不查找也不载入任何旧任务资料" in section


def test_same_task_recovery_checks_context_and_skips_unaffected_done_items() -> None:
    section = _flow(_section(_read(RECOVERY), "## 同一任务恢复"))

    assert "核对证券、`as_of`、数据模式、授权和用户最新要求" in section
    assert "不重新执行无影响的已完成项" in section
    assert "失败历史" in section


def test_stale_phase2_boundary_sentence_is_removed_everywhere() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*.md"))

    assert "不是二期契约" not in corpus
