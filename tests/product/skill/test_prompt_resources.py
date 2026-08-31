import re
from pathlib import Path

import pytest
import yaml

ROOT = Path("skills/hetu-stock-analysis")


def _read(relative: str) -> str:
    return ROOT.joinpath(relative).read_text(encoding="utf-8")


def test_skill_uses_exact_phase_two_frontmatter_and_section_order() -> None:
    text = _read("SKILL.md")
    raw, body = text[4:].split("\n---\n", 1)
    assert yaml.safe_load(raw) == {
        "name": "hetu-stock-analysis",
        "description": (
            "Analyze one China A-share stock with public or explicitly authorized data "
            "when the user asks for evidence-based company research; exclude industry-only, "
            "portfolio, comparison, non-A-share, automatic trading, and pure order-execution "
            "requests."
        ),
    }
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 适用边界",
        "## 运行前提与安全优先级",
        "## Agent 控制循环",
        "## 默认值与用户可见进展",
        "## 按需资源导航",
        "## 工作包直接导航",
        "## 最终交付",
    ]
    for target in (
        "references/artifact-contract.md",
        "references/work-package-result.md",
        "references/report-guidance.md",
        "references/tool-catalog.md",
        "references/source-contracts.md",
    ):
        assert f"]({target})" in text
    assert "实际分析模型" in text
    assert "保留中间脚本" in text


def test_skill_defaults_continue_without_overquestioning() -> None:
    text = _read("SKILL.md")
    for phrase in (
        "候选任务卡",
        "正式任务卡",
        "public",
        "standard",
        "任务发起时点",
        "基础全面覆盖",
        "Markdown 报告",
        "默认值",
        "待核验",
        "唯一映射",
        "歧义",
        "异常",
    ):
        assert phrase in text
    assert "在主体核验前" in text
    assert "向用户披露" in text
    assert re.search(r"唯一\s+唯一映射", text) is None


def test_deep_runtime_quality_rules_match_the_approved_design() -> None:
    skill = _read("SKILL.md")
    assert "`deep`" in skill
    assert "各工作包选择具体来源时" in skill
    assert "references/source-contracts.md" in skill

    required = {
        "references/artifact-contract.md": (
            "<证券简称>-<证券代码>-<请求深度>-<任务时间>",
            "quick",
            "standard",
            "deep",
        ),
        "references/work-packages/core/W0-task-framing.md": (
            "W1",
            "唯一核验",
            "正式研究目录",
        ),
        "references/work-packages/core/W1-subject-verification.md": (
            "证券简称：",
            "权威身份：",
            "run_id",
        ),
        "references/work-packages/core/W2-incremental-events.md": (
            "连续",
            "日期空档",
            "部分覆盖",
            "公告并集",
            "重复数量",
            "风险扫描",
            "同一公告并集",
        ),
        "references/work-packages/core/W3-industry-competition.md": (
            "最新官方",
            "WFE",
            "设备总销售额",
            "候选",
            "每个具体指标",
            "最新适用版本",
            "允许并存",
        ),
        "references/work-packages/core/W5-financial-validation.md": (
            "实际本地输入",
            "机械复算",
            "降级",
            "superseded",
            "failed",
            "同一次修正",
            "所有直接引用",
        ),
        "references/work-packages/core/W4-business-governance.md": (
            "行业特定",
            "ClinicalTrials.gov",
            "登记不等于",
            "同一控制下企业合并",
            "非同一控制下企业合并",
            "逐实体",
            "不得跨行",
        ),
        "references/work-packages/core/W7-valuation-expectations.md": (
            "方法",
            "输入",
            "公式",
            "限制",
            "不作为本轮",
            "numeric_consistency.py",
            "必须优先",
            "手算",
            "临时脚本",
        ),
        "references/work-packages/core/W10-report-review.md": (
            "manifest",
            "checkpoint",
            "所有现存工作包引用",
            "纯格式提示",
            "最新采用集合",
            "不得继续引用",
            "同一份实际清单",
        ),
    }
    for relative, phrases in required.items():
        text = _read(relative)
        assert all(phrase in text for phrase in phrases), (relative, phrases)


def test_runtime_completion_rules_cover_observed_dual_model_failures() -> None:
    required = {
        "references/work-packages/core/W2-incremental-events.md": (
            "定稿前",
            "各段原始数量",
            "跨段重复数量",
            "去重后并集数量",
            "风险扫描输入",
        ),
        "references/work-packages/core/W3-industry-competition.md": (
            "本次运行",
            "候选集合",
            "历史运行",
            "不参与版本裁决",
        ),
        "references/work-packages/core/W4-business-governance.md": (
            "附注标题层级",
            "该实体所在行",
            "合并类型",
            "不得关闭",
        ),
        "references/work-packages/core/W5-financial-validation.md": (
            "当前采用集合",
            "退出采用集合",
            "实际输入链",
        ),
        "references/work-packages/core/W7-valuation-expectations.md": (
            "每项计算",
            "canonical 产物路径",
            "不支持或不可用原因",
        ),
        "references/work-packages/core/W10-report-review.md": (
            "terminal status",
            "先排除",
            "逐条核对",
            "实质提示",
        ),
    }
    for relative, phrases in required.items():
        text = _read(relative)
        assert all(phrase in text for phrase in phrases), (relative, phrases)


def test_excluded_requests_short_circuit_before_research() -> None:
    text = _read("SKILL.md")

    for phrase in (
        "只读取本文件判定适用边界",
        "立即停止本 Skill",
        "不得继续读取编排、证据、检查点或工作包资源",
        "不得检索研究数据",
        "不得建立研究产物",
        "不得调用 HETU helper 或 legacy",
        "简短说明不适用",
        "安全下一步",
        "立即给出本轮最终答复",
        "不得改用其他 Skill、工具或方法继续完成原请求",
        "另行提出受支持的单只 A 股研究请求",
    ):
        assert phrase in text


def test_skill_keeps_agent_in_control_and_helpers_local() -> None:
    text = _read("SKILL.md")
    for phrase in (
        "自由",
        "开始前依赖",
        "定稿前依赖",
        "共享基准",
        "回访",
        "原子助手",
        "局部",
        "大模型 Agent 不可用",
        "终止",
    ):
        assert phrase in text


def test_orchestration_defines_agent_led_nonserial_loop() -> None:
    text = _read("references/orchestration.md")
    for phrase in (
        "未开始",
        "进行中",
        "已覆盖",
        "部分覆盖",
        "无法取得",
        "存在冲突",
        "不适用",
        "开始前依赖",
        "定稿前依赖",
        "共享基准",
        "回访触发",
        "自由编排",
        "临时研究子问题",
        "递归重开",
        "用户可见进展",
        "W10",
        "停止条件",
    ):
        assert phrase in text
    assert "编号不代表顺序" in text


@pytest.mark.parametrize(
    "subject_ref",
    ("600519", "600519.SH", "000001.SZ", "430047.BJ"),
)
def test_subject_verification_contract_accepts_a_share_identifiers(
    subject_ref: str,
) -> None:
    text = _read("references/work-packages/core/W1-subject-verification.md")

    assert f"`{subject_ref}`" in text


@pytest.mark.parametrize(
    "subject_ref",
    ("00700.HK", "AAPL", "AAPL.US", "BRK.B", "NASDAQ:AAPL", "600519.sh"),
)
def test_subject_verification_contract_rejects_non_a_share_identifiers(
    subject_ref: str,
) -> None:
    text = _read("references/work-packages/core/W1-subject-verification.md")

    assert f"`{subject_ref}`" in text


@pytest.mark.parametrize(
    "unsafe_shape",
    ("换行标题", "HTML 标签", "Markdown 链接", "表格竖线", "路径片段"),
    ids=("newline-heading", "html-tag", "markdown-link", "table-pipe", "path-fragment"),
)
def test_subject_verification_contract_rejects_structure_injection(
    unsafe_shape: str,
) -> None:
    text = _read("references/work-packages/core/W1-subject-verification.md")

    assert unsafe_shape in text


def test_evidence_rules_cover_source_time_access_and_gap_contracts() -> None:
    text = _read("references/evidence-rules.md")
    for phrase in (
        "fact",
        "calculation",
        "forecast",
        "judgment",
        "unknown",
        "L0",
        "L1",
        "L2",
        "原始生产者",
        "传播渠道",
        "独立性",
        "事件时间",
        "发布时间",
        "采集时间",
        "as_of",
        "报告生成时间",
        "仅有日期",
        "合法",
        "替代路径",
        "缺口",
        "public",
        "authorized",
        "引用",
    ):
        assert phrase in text
    assert text.index("L0") < text.index("L1") < text.index("L2")
    for boundary in ("登录", "验证码", "付费墙", "robots", "许可"):
        assert boundary in text
    assert "同一原始" in text and "一个" in text


def test_evidence_rules_close_material_fact_scope_and_search_boundaries() -> None:
    text = _read("references/evidence-rules.md")
    for phrase in (
        "重大事实",
        "主体",
        "指标",
        "数值与单位",
        "归属期间或基准日",
        "报表范围",
        "来源原文定位",
        "截断",
        "来源未声明",
    ):
        assert phrase in text


def test_evidence_rules_use_latest_adjusted_comparable_series() -> None:
    text = _read("references/evidence-rules.md")
    for phrase in (
        "追溯调整",
        "最新正式披露",
        "调整后可比列",
        "不得混用",
        "L0",
    ):
        assert phrase in text


def test_evidence_rules_preserve_ratio_unit_semantics_and_recalculate() -> None:
    text = _read("references/evidence-rules.md")
    for phrase in (
        "百分数",
        "小数",
        "基点",
        "不得重复乘以 100",
        "绝对值复算",
        "存在冲突",
    ):
        assert phrase in text


def test_w4_distinguishes_acquiree_and_consolidation_scopes() -> None:
    text = _read("references/work-packages/core/W4-business-governance.md")
    for phrase in (
        "取得控制权",
        "纳入合并财务报表",
        "全年公司数据",
        "购买日至报告期末",
        "合并成本",
        "可辨认净资产",
        "商誉",
        "部分覆盖",
        "W5",
        "W6",
        "W7",
        "W9",
        "W10",
    ):
        assert phrase in text


def test_w4_requires_explicit_acquisition_fact_closure_slots() -> None:
    text = _read("references/work-packages/core/W4-business-governance.md")
    for phrase in (
        "并购事实闭包",
        "控制权取得日",
        "实际并表日",
        "购买日至期末收入",
        "购买日至期末净利润",
        "取得的可辨认净资产公允价值份额",
        "勾稽",
    ):
        assert phrase in text


def test_w10_requires_claim_source_period_and_scope_semantic_review() -> None:
    text = _read("references/work-packages/core/W10-report-review.md")
    for phrase in (
        "关键主张",
        "来源原文",
        "期间",
        "范围",
        "逐条",
        "重开受影响工作包",
        "不得交付",
    ):
        assert phrase in text


def test_w10_requires_itemized_closure_and_independent_recalculation() -> None:
    text = _read("references/work-packages/core/W10-report-review.md")
    for phrase in (
        "主题出现",
        "不能证明",
        "适用重大事实",
        "逐项闭合",
        "独立复算",
        "原始绝对值",
        "单位语义",
    ):
        assert phrase in text


def test_w10_keeps_monitoring_metric_and_denominator_consistent() -> None:
    text = _read("references/work-packages/core/W10-report-review.md")
    for phrase in (
        "指标名称",
        "公式或分母",
        "基准值",
        "方向",
        "窗口",
        "来源",
        "不得混用",
    ):
        assert phrase in text


def test_checkpoint_is_human_readable_and_not_an_execution_state_machine() -> None:
    text = _read("references/checkpoint.md")
    for phrase in (
        "任务边界",
        "宿主能力",
        "工作包状态",
        "事实",
        "判断",
        "反证",
        "冲突",
        "未知",
        "替代路径",
        "用户决定",
        "编排",
        "回访",
        "两轮自检",
        "下一研究意图",
        "不得保存或展示隐藏思维链",
    ):
        assert phrase in text
    for forbidden in ("阶段游标", "自动迁移", "下一动作指令"):
        assert forbidden in text


def test_recovery_prefers_legal_local_recovery_and_preserves_history() -> None:
    text = _read("references/recovery.md")
    for phrase in (
        "自动补查",
        "强制暂停",
        "暂停说明",
        "用户决定",
        "接受限制",
        "不能",
        "支持证据",
        "同一任务",
        "失败历史",
        "大模型 Agent 不可用",
        "终止",
    ):
        assert phrase in text


def test_report_guidance_keeps_business_writing_with_the_agent() -> None:
    text = _read("references/report-guidance.md")
    for phrase in (
        "Agent 直接撰写",
        "任务与时点",
        "核心发现",
        "公司、业务与行业",
        "财务",
        "估值",
        "市场",
        "反证",
        "监控",
        "数据覆盖",
        "来源",
        "用户可读引用",
        "技术完成状态",
        "利润与现金",
        "经营驱动",
        "研究自检",
        "安全自检",
        "重新执行两轮自检",
        "实际交付",
    ):
        assert phrase in text
    assert "内部证据 ID 不能替代" in text
    for action in ("买入", "卖出", "加仓", "减仓", "持有", "退出", "仓位"):
        assert action in text


def test_agent_facing_contract_explains_checker_compatible_shapes() -> None:
    report_guidance = _read("references/report-guidance.md")
    artifact_contract = _read("references/artifact-contract.md")
    assert "首页元数据表头使用 `| 字段 | 规则 |` 或等价的" in report_guidance
    assert "`| 字段 | 内容 |`" in report_guidance
    for phrase in (
        "解析器接受 `1`、`第 1 章`、`1. 任务与时点`、`2–3` 等等价章节形式",
        "`W2–W9` 范围",
        "关键主张定位应是目标章节可搜索原文",
        "证据定位应使用存在的证据编号、fragment",
        "纯任务元数据或没有 manifest 产物链的边界说明不要写入映射表",
    ):
        assert phrase in artifact_contract


def test_report_guidance_requires_safe_markdown_table_text() -> None:
    text = _read("references/report-guidance.md")

    for phrase in ("Markdown 表格", "竖线", "反斜杠", "换行"):
        assert phrase in text


def test_report_guidance_rejects_structure_injection() -> None:
    text = _read("references/report-guidance.md")

    for phrase in ("危险链接", "原始 HTML", "非预期文件引用", "不得原样输出"):
        assert phrase in text


def test_report_guidance_redacts_authorized_locators() -> None:
    text = _read("references/report-guidance.md")

    for phrase in ("authorized", "受限 locator", "安全范围描述"):
        assert phrase in text


def test_report_guidance_preserves_verified_public_locator_as_literal_markdown() -> None:
    text = _read("references/report-guidance.md")

    for phrase in ("public locator", "核验", "字面 Markdown"):
        assert phrase in text


def test_monitoring_ambiguity_yields_until_confirmed() -> None:
    text = _read("references/report-guidance.md")
    for phrase in (
        "原始表达",
        "分母",
        "方向",
        "连续期数",
        "AND/OR",
        "歧义",
        "确认前",
        "不得自行选定",
    ):
        assert phrase in text


def test_host_tools_keep_files_and_optional_helpers_at_atomic_scope() -> None:
    text = _read("references/host-tools.md")
    for phrase in (
        "能力发现",
        "宿主原生",
        "原子助手",
        "可选",
        "局部失败",
        ".hetu/research/<证券简称>-<证券代码>-<请求深度>-<任务时间>/",
        "checkpoint.md",
        "evidence.md",
        "artifacts/",
        "report.md",
        "合法取得",
        "一期",
        "不可信",
        "secret",
        "不得保存或展示隐藏思维链",
        "工作区固定为",
        "不得使用其他位置",
    ):
        assert phrase in text
    assert "等价安全位置" not in text


CLOSED_SOURCE_IDS = (
    "cninfo-announcement-index",
    "sina-financial-statements",
    "tencent-quote-snapshot",
)

RESEARCH_ROOT_PHRASES = (
    "调用前创建或选择",
    ".hetu/research/<证券简称>-<证券代码>-<请求深度>-<任务时间>/",
    "`--input` 和 `--output`",
    "同一当前研究根",
    "`artifacts/raw/`",
    "不接受研究根参数",
    "目录选择和边界确认由 Agent 负责",
)


@pytest.mark.parametrize(
    ("resource", "phrases"),
    (
        pytest.param(
            "SKILL.md",
            ("source_fetch.py", *CLOSED_SOURCE_IDS),
            id="skill-optional-explicit-tool",
        ),
        pytest.param(
            "references/source-contracts.md",
            (
                "source_fetch.py",
                *CLOSED_SOURCE_IDS,
                "不自动换源",
                "采集成功不代表采用",
                "由 Agent 决定是否调用替代",
                "主体、期间、范围、单位和用途",
                "不得后台自动换源",
                "精确公司份额",
                "未披露订单",
            ),
            id="source-contracts-agent-owned",
        ),
        pytest.param(
            "references/host-tools.md",
            (
                "source_fetch.py",
                *CLOSED_SOURCE_IDS,
                "不自动换源",
                "采集成功不代表采用",
                "任意 URL",
                "其他搜索、浏览、PDF",
                *RESEARCH_ROOT_PHRASES,
            ),
            id="host-tools-fetcher-and-root",
        ),
        pytest.param(
            "references/tool-catalog.md",
            (
                "source_fetch.py",
                *CLOSED_SOURCE_IDS,
                "不自动换源",
                *RESEARCH_ROOT_PHRASES,
            ),
            id="tool-catalog-closed-entry",
        ),
    ),
)
def test_source_fetch_stays_an_optional_agent_owned_closed_contract(
    resource: str, phrases: tuple[str, ...]
) -> None:
    text = _read(resource)
    for phrase in phrases:
        assert phrase in text, f"{resource} lost source-fetch contract phrase {phrase!r}"
