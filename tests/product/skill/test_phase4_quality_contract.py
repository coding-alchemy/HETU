from pathlib import Path

ROOT = Path("skills/hetu-stock-analysis")


def read(relative: str) -> str:
    return ROOT.joinpath(relative).read_text(encoding="utf-8")


def test_skill_loads_phase4_rules_only_when_needed() -> None:
    skill = read("SKILL.md")
    for relative in (
        "references/company-models.md",
        "references/research-quality.md",
    ):
        assert f"]({relative})" in skill
    for phrase in (
        "W1 唯一核验后",
        "形成或修订公司经营模型时",
        "选择或复核方法时",
        "进入 W9",
        "进入 W10",
        "按需读取",
    ):
        assert phrase in skill
    assert "启动时读取[公司经营模型规则]" not in skill
    assert "启动时读取[研究质量规则]" not in skill


def test_research_quality_rule_keeps_four_inputs_and_five_outputs() -> None:
    text = read("references/research-quality.md")
    for phrase in (
        "共通底线＋请求深度＋公司经营模型＋特殊状态",
        "有",
        "替代取得",
        "无",
        "未发现",
        "未取得",
        "不适用",
        "存在冲突",
        "实际深度",
        "完整",
        "不完整",
        "有效",
        "受限",
        "未达到 quick",
        "关键资料缺口",
        "当前不能得出的结论",
        "继续交付",
    ):
        assert phrase in text


def test_company_model_rule_covers_six_nonexclusive_models() -> None:
    text = read("references/company-models.md")
    for phrase in (
        "金融",
        "周期",
        "重研发",
        "平台或订阅",
        "资产或资本密集",
        "混合业务",
        "主要经营模型",
        "次要模型或适用分部",
        "易误用或不适用方法",
        "不得由 Python",
    ):
        assert phrase in text


def test_owner_contract_uses_existing_twelve_sections() -> None:
    text = read("references/work-package-result.md")
    assert "必需项 | 要求来源 | 状态 | 证据或查询范围 | 对结论的影响" in text
    assert "目标、职责和实际覆盖" in text
    assert "满足：" in text
    assert "未满足：" in text
    assert "不新增第十三节" in text


def test_quality_responsibilities_stay_with_existing_owners() -> None:
    required = {
        "references/work-packages/core/W4-business-governance.md": (
            "主要经营模型",
            "次要模型或适用分部",
            "W3",
        ),
        "references/work-packages/core/W5-financial-validation.md": (
            "使用或排除理由",
            "所需输入",
            "实际输入",
        ),
        "references/work-packages/core/W6-forecast-scenarios.md": (
            "使用或排除理由",
            "所需输入",
            "实际输入",
        ),
        "references/work-packages/core/W7-valuation-expectations.md": (
            "使用或排除理由",
            "所需输入",
            "实际输入",
        ),
        "references/work-packages/core/W9-thesis-counterevidence.md": (
            "当前不能得出的结论",
            "恢复条件",
        ),
        "references/work-packages/core/W10-report-review.md": (
            "信息完整性",
            "分析有效性",
            "实际深度",
        ),
    }
    for relative, phrases in required.items():
        text = read(relative)
        assert all(phrase in text for phrase in phrases), (relative, phrases)


def test_model_specific_requirements_are_routed_to_actual_owners() -> None:
    w4 = read("references/work-packages/core/W4-business-governance.md")
    assert "分配给 W1–W8 的实际 owner" in w4
    assert "W4 本包必需项表只记录本包职责内的项目" in w4
    assert "并入本包必需项表" not in w4


def test_report_and_checkpoint_expose_quality_without_a_second_detail_store() -> None:
    report = read("references/report-guidance.md")
    checkpoint = read("references/checkpoint.md")
    for phrase in (
        "请求深度",
        "实际深度",
        "信息完整性",
        "分析有效性",
        "主要经营模型",
        "关键资料缺口",
        "当前不能得出的结论",
    ):
        assert phrase in report
    for phrase in (
        "质量字段",
        "当前状态",
        "请求深度",
        "实际深度",
        "信息完整性",
        "分析有效性",
        "主要经营模型",
        "不复制 W1–W8 必需项明细",
    ):
        assert phrase in checkpoint


def test_mechanical_write_contracts_are_pinned_after_behavior_findings() -> None:
    research_quality = read("references/research-quality.md")
    for phrase in (
        "只写七种状态的裸值本身",
        "词与冒号之间不得插入任何字",
        "逐字为\n允许值本身",
    ):
        assert phrase in research_quality
    w10 = read("references/work-packages/core/W10-report-review.md")
    assert "定稿前结构自检" in w10
    assert "修复后重跑自检直至这些 issues 清零" in w10
    for phrase in (
        "发布输入只能来自各 owner 的 adopted 产物",
        "不得登记为 adopted",
        "canonical 工具失败信封",
        "对应 owner 的 adopted `derived` 或 `script` 产物",
        "标为满足",
        "manifest.run.data_mode",
        "无产物\n时删除该声明",
        "通读各 owner 工作包正文",
        "否定性结论",
        "真实存在于\nevidence.md",
    ):
        assert phrase in w10
    assert "必须同步体现为必需项表的未满足行" in research_quality
    result_contract = read("references/work-package-result.md")
    for phrase in (
        "逐字相同，不加括注",
        "只写七种状态裸值",
        "方法选择表固定放于本节",
    ):
        assert phrase in result_contract
    report = read("references/report-guidance.md")
    for phrase in (
        "逐字为允许值本身",
        "不得自创同义列名或在表头追加\n单位或说明",
    ):
        assert phrase in report
    artifact_contract = read("references/artifact-contract.md")
    for phrase in (
        "三处必须逐字相同",
        "`MANIFEST.json` 文件本身的 SHA-256",
        "只能是 `json` 或 `csv`",
        "不得填写目录路径、外部端点或描述文字",
        "`not_adopted` 登记；`failed` 仅限真实执行失败",
        "不得构成环路",
        "无本地输入时写空数组 `[]`",
        "不得在 `<source-id>` 或 `<work-package-id>` 之下再建子目录",
        "不得把非 UTF-8 或不可解析内容登记为 `txt`/`json`",
    ):
        assert phrase in artifact_contract


def test_final_review_closes_negative_and_execution_claims_against_adopted_evidence() -> None:
    evidence_rules = read("references/evidence-rules.md")
    for phrase in (
        "未抽取或未定位不能改写为“公司未披露”",
        "主题词和合理同义词",
        "核对全部命中原文",
        "每个点名来源",
        "失败尝试不能支撑成功执行声明",
    ):
        assert phrase in evidence_rules

    research_quality = read("references/research-quality.md")
    for phrase in (
        "相关 `adopted` 产物中检索主题词和合理同义词",
        "核对全部命中原文",
        "公司未披露",
        "本次未抽取或未定位",
    ):
        assert phrase in research_quality

    w2 = read("references/work-packages/core/W2-incremental-events.md")
    for phrase in (
        "公告并集中的标题命中",
        "不得写成无此类事件",
    ):
        assert phrase in w2

    w10 = read("references/work-packages/core/W10-report-review.md")
    for phrase in (
        "发布前声明核对",
        "内容否定",
        "执行声明",
        "`report.md`、`checkpoint.md`、`evidence.md` 和 W0–W9",
        "相关 `adopted` 产物中检索主题词和合理同义词",
        "核对全部命中",
        "每个点名来源或动作",
        "无论结果为成功、失败还是空返回",
        "不得登记为 `adopted` 业务数据",
        "没有可核验的接口探测记录，不能判断",
        "执行声明先清点再核对",
        "关键词命中不构成裁决",
        "失败记录可以证明对应动作执行失败",
        "不能证明成功，也不能作为业务事实或计算依据",
        "不得引用 superseded 或 not_adopted",
        "只能作为执行声明与失败\n披露的记录引用（来源覆盖表与缺口说明等过程披露位置）",
        "不得作为业务事实或计算依据进入\n报告数据表",
    ):
        assert phrase in w10

    for phrase in (
        "必须有可核验记录支撑",
        "不得叙述未留痕的探测过程",
    ):
        assert phrase in research_quality
