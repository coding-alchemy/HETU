import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path("skills/hetu-stock-analysis")

REPORT_SECTIONS = (
    "1. 任务与时点",
    "2. 核心发现",
    "3. 公司、业务与行业",
    "4. 治理、审计、资本配置与重大事件",
    "5. 财务验证与经营质量",
    "6. 预测与受限情景",
    "7. 估值与隐含预期",
    "8. 市场状态与近期信号",
    "9. 论点、反证、未知与条件",
    "10. 监控建议",
    "11. 数据覆盖、缺口、冲突与来源",
    "12. 最终边界",
)

RESULT_HEADINGS = (
    "## 任务与运行元数据",
    "## 目标、职责和实际覆盖",
    "## 输入、版本及共享基准",
    "## 已核验事实与证据定位",
    "## 计算输入、公式、结果和单位",
    "## 分析判断及事实依据",
    "## 缺口、冲突、失败和替代路径",
    "## 下游输出、报告章节和回访条件",
    "## 中间脚本及产物清单",
    "## 模型补充发现",
    "## 完成边界与自检",
    "## 修订记录",
)


def read(relative: str) -> str:
    return ROOT.joinpath(relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    (
        "references/artifact-contract.md",
        "references/work-package-result.md",
        "references/report-guidance.md",
        "references/tool-catalog.md",
        "references/source-contracts.md",
    ),
)
def test_phase2_resource_exists(relative: str) -> None:
    assert ROOT.joinpath(relative).is_file()


def test_report_contract_has_exact_twelve_chapter_floor() -> None:
    text = read("references/report-guidance.md")
    indexes = [text.index(section) for section in REPORT_SECTIONS]
    assert indexes == sorted(indexes)
    for phrase in (
        "分析模型",
        "推理深度",
        "Markdown 表格",
        "模型补充发现",
        "连续编号的三级小节",
        "补充表格",
        "不得新增、改名、删除或重排二级章节",
        "report.md 之外的独有事实",
    ):
        assert phrase in text


def test_work_package_result_contract_is_closed_and_safe() -> None:
    text = read("references/work-package-result.md")
    indexes = [text.index(heading) for heading in RESULT_HEADINGS]
    assert indexes == sorted(indexes)
    for phrase in (
        "run_id",
        "实际模型",
        "不得保存隐藏思维链或 secret",
        "当前工作包职责",
        "实际 owner",
        "不得复制并取代",
        "十二节必须按序连续出现",
        "无适用内容时保留节标题并写正式状态语义与原因",
        "不得删除或省略任何节标题",
    ):
        assert phrase in text
    assert "省略本节" not in text


def test_work_package_ownership_direction_w7_references_w8() -> None:
    w7 = read("references/work-packages/core/W7-valuation-expectations.md")
    w8 = read("references/work-packages/core/W8-market-signals.md")
    assert "市场输入引用 W8" in w7
    assert "引用 W7" not in w8
    assert "W7 → W8" in w8


def test_source_contracts_keep_preadoption_gate_closed() -> None:
    text = read("references/source-contracts.md")
    assert "阶段 04 完成前不得标记替代取得" in text
    assert "只适用于宿主原生合法替代" not in text
    assert '都不得标记"替代取得"' in text


ADOPTED_TOOL_SCRIPTS = (
    "financial_statements.py",
    "announcement_index.py",
    "numeric_consistency.py",
    "financial-ratio-series.py",
    "market-series-metrics.py",
)

CATALOG_ENTRY_FIELDS = (
    "状态：`adopted`",
    "文件路径：",
    "用途：",
    "输入 Schema：",
    "输出 Schema：",
    "失败异常：",
    "是否可离线重放：",
    "禁止边界：",
    "实际测试文件：",
)


def _tool_catalog_section(filename: str) -> str:
    text = read("references/tool-catalog.md")
    marker = f"### {filename}\n"
    start = text.find(marker)
    assert start != -1, f"tool-catalog.md lost the entry for {filename}"
    end = text.find("\n### ", start + len(marker))
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.parametrize("filename", ADOPTED_TOOL_SCRIPTS)
def test_tool_catalog_registers_each_adopted_tool_completely(filename: str) -> None:
    assert ROOT.joinpath("scripts", filename).is_file()
    section = _tool_catalog_section(filename)
    for field in CATALOG_ENTRY_FIELDS:
        assert field in section, f"{filename} entry lacks {field!r}"


def test_tool_catalog_carries_the_closed_boundary_phrases() -> None:
    text = read("references/tool-catalog.md")
    for phrase in (
        "不选择来源",
        "不决定换源",
        "不裁决冲突",
        "不生成报告结论",
        "不生成交易信号",
    ):
        assert phrase in text


def test_report_guidance_carries_full_tier_coverage_contract() -> None:
    text = read("references/report-guidance.md")
    for phrase in (
        "`quick`：主体与交易状态",
        "`standard`：在 `quick` 基础上覆盖控制关系",
        "`deep`：在 `standard` 基础上覆盖更长历史",
        "跨 ≥4 个报告期（含最新季报）的多指标财务主表",
        "至少 1 组估值倍数与 1 组三情景表",
        "带日期或公告定位",
        "优先绑定监控或失效条件",
        "阈值、窗口与复查动作",
        "行为基线在 G2 首次建立",
    ):
        assert phrase in text


def test_artifact_contract_freezes_artifact_object_schema() -> None:
    text = read("references/artifact-contract.md")
    for phrase in (
        "不得使用下述集合之外的任何键",
        '"path"',
        '"type"',
        '"work_package"',
        '"inputs"',
        '"status"',
        "type=script",
        "status=failed",
        "非 script 条目**不得包含**该键",
        "非 failed 条目**不得包含**该键",
        "确定性计算不得遗漏实际输入",
        "manifest 完整输入哈希为权威身份",
    ):
        assert phrase in text


BASE_ARTIFACT_KEYS = {
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

SCRIPT_SUBKEYS = {
    "purpose",
    "safe_call",
    "dependencies",
    "environment",
    "input",
    "output",
    "exit_status",
    "executed_at",
}


def _artifact_entry_example() -> dict:
    text = read("references/artifact-contract.md")
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    entry_blocks = [
        block
        for block in blocks
        if '"inputs"' in block and '"artifacts/raw/' in block
    ]
    assert entry_blocks, "contract must carry the frozen artifact entry example"
    return json.loads(entry_blocks[0])


def _aggregate_hash8(entry: dict) -> str:
    ordered = sorted(entry["inputs"], key=lambda i: i["path"])
    if len(ordered) == 1:
        return ordered[0]["sha256"][:8]
    digest_text = "".join(item["sha256"] for item in ordered)
    return hashlib.sha256(digest_text.encode()).hexdigest()[:8]


def _schema_issues(entry: dict) -> list[str]:
    issues: list[str] = []
    for key in BASE_ARTIFACT_KEYS - entry.keys():
        issues.append(f"missing:{key}")
    for key in entry.keys() - BASE_ARTIFACT_KEYS - {"script", "failure"}:
        issues.append(f"extra:{key}")
    if entry.get("type") == "script":
        if "script" not in entry:
            issues.append("missing:script")
        elif not entry["script"].keys() >= SCRIPT_SUBKEYS:
            issues.append("script-subkey-missing")
    elif "script" in entry:
        issues.append("script-on-non-script")
    if entry.get("status") == "failed" and "failure" not in entry:
        issues.append("missing:failure")
    if entry.get("status") != "failed" and "failure" in entry:
        issues.append("failure-on-non-failed")
    inputs = entry.get("inputs")
    if not isinstance(inputs, list):
        issues.append("inputs-not-list")
        return issues
    for item in inputs:
        if set(item) != {"path", "sha256"} or ".." in item["path"]:
            issues.append("bad-input-item")
    if entry.get("type") in {"normalized", "derived"} and not inputs:
        issues.append(f"inputs-empty-for-{entry.get('type')}")
    if entry.get("type") == "script" and "script" in entry:
        declared = entry["script"].get("input")
        if declared and not any(item["path"] == declared for item in inputs):
            issues.append("script-input-not-registered")
        if declared and not inputs:
            issues.append("inputs-empty-for-script")
    if entry.get("type") == "derived" and inputs:
        match = re.search(r"--([0-9a-f]{8})--calc-v", entry.get("path", ""))
        if match and match.group(1) != _aggregate_hash8(entry):
            issues.append("derived-hash8-mismatch")
    return issues


def test_contract_example_uses_exactly_the_frozen_base_keys() -> None:
    entry = _artifact_entry_example()
    assert set(entry) == BASE_ARTIFACT_KEYS


def test_schema_validator_accepts_valid_entries_including_multi_input() -> None:
    valid_script = {
        **{key: None for key in BASE_ARTIFACT_KEYS},
        "path": "artifacts/scripts/W5/x.py",
        "type": "script",
        "status": "adopted",
        "inputs": [
            {"path": "artifacts/normalized/W5/a.json", "sha256": "0" * 64},
            {"path": "artifacts/normalized/W5/b.json", "sha256": "1" * 64},
        ],
        "script": {
            key: ("artifacts/normalized/W5/a.json" if key == "input" else "x")
            for key in SCRIPT_SUBKEYS
        }
        | {"exit_status": 0},
    }
    valid_derived = {
        **{key: None for key in BASE_ARTIFACT_KEYS},
        "path": f"artifacts/derived/W5/demo-check--{'0'*8}--calc-v1.json",
        "type": "derived",
        "status": "adopted",
        "inputs": [{"path": "artifacts/normalized/W5/a.json", "sha256": "0" * 64}],
    }
    valid_failed = {
        **{key: None for key in BASE_ARTIFACT_KEYS},
        "path": "artifacts/raw/s/f.json",
        "type": "raw",
        "status": "failed",
        "inputs": [],
        "failure": "transport_error",
    }
    assert _schema_issues(valid_script) == []
    assert _schema_issues(valid_derived) == []
    assert _schema_issues(valid_failed) == []


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    (
        ({"custom_key": 1}, "extra:custom_key"),
        ({"type": "raw", "script": {}}, "script-on-non-script"),
        ({"status": "adopted", "failure": "x"}, "failure-on-non-failed"),
        (
            {"status": "failed"},
            "missing:failure",
        ),
        ({"inputs": [{"path": "../escape.json"}]}, "bad-input-item"),
        ({"inputs": [{"path": "a.json"}]}, "bad-input-item"),
        ({"type": "derived"}, "inputs-empty-for-derived"),
        ({"type": "normalized"}, "inputs-empty-for-normalized"),
        (
            {
                "type": "script",
                "script": {
                    key: ("artifacts/normalized/W5/other.json" if key == "input" else "x")
                    for key in SCRIPT_SUBKEYS
                }
                | {"exit_status": 0},
                "inputs": [{"path": "artifacts/normalized/W5/a.json", "sha256": "0" * 64}],
            },
            "script-input-not-registered",
        ),
        (
            {
                "type": "derived",
                "path": "artifacts/derived/W5/demo-check--deadbeef--calc-v1.json",
                "inputs": [{"path": "artifacts/normalized/W5/a.json", "sha256": "0" * 64}],
            },
            "derived-hash8-mismatch",
        ),
    ),
)
def test_schema_validator_rejects_contract_violations(
    mutation: dict, expected_issue: str
) -> None:
    entry = {
        **{key: None for key in BASE_ARTIFACT_KEYS},
        "path": "artifacts/raw/s/f.json",
        "type": "raw",
        "status": "adopted",
        "inputs": [],
    }
    entry.update(mutation)
    assert expected_issue in _schema_issues(entry)


DEMO_ROOT = Path(
    ".hetu/validation/phase-2-hardening/20260821T133021+0800-stage02-demo/research-skeleton"
)


@pytest.mark.skipif(not DEMO_ROOT.is_dir(), reason="stage 02 demo batch not present")
def test_stage02_demo_manifest_satisfies_the_frozen_schema() -> None:
    manifest = json.loads((DEMO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["artifacts"]
    assert entries, "demo manifest must not be empty"
    paths = {entry["path"] for entry in entries}
    for entry in entries:
        issues = _schema_issues(entry)
        assert issues == [], f"schema issues in {entry['path']}: {issues}"
        file_path = DEMO_ROOT / entry["path"]
        assert file_path.is_file(), f"missing file for {entry['path']}"
        assert (
            hashlib.sha256(file_path.read_bytes()).hexdigest() == entry["sha256"]
        ), f"hash mismatch for {entry['path']}"
        for item in entry["inputs"]:
            assert item["path"] in paths, f"unregistered input {item['path']}"
            assert (
                hashlib.sha256((DEMO_ROOT / item["path"]).read_bytes()).hexdigest()
                == item["sha256"]
            ), f"input hash mismatch for {item['path']}"


@pytest.mark.skipif(not DEMO_ROOT.is_dir(), reason="stage 02 demo batch not present")
def test_stage02_demo_trace_chain_resolves_to_real_entries() -> None:
    manifest = json.loads((DEMO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    by_path = {entry["path"]: entry for entry in manifest["artifacts"]}
    evidence = (DEMO_ROOT / "evidence.md").read_text(encoding="utf-8")
    for entry_ref in re.findall(r"`(artifacts/[^`]+)`", evidence):
        assert entry_ref in by_path, f"evidence references unknown entry {entry_ref}"
        if match := re.search(r"manifest 状态 (\w+)", evidence.split(entry_ref)[1][:80]):
            assert match.group(1) == by_path[entry_ref]["status"], (
                f"evidence status mismatch for {entry_ref}"
            )
    for name, content in (
        ("report.md", (DEMO_ROOT / "report.md").read_text(encoding="utf-8")),
        (
            "W10",
            (
                DEMO_ROOT / "work-packages/W10-report-review.md"
            ).read_text(encoding="utf-8"),
        ),
    ):
        for entry_ref in re.findall(r"`(artifacts/[^`]+)`", content):
            assert entry_ref in by_path, f"{name} references unknown entry {entry_ref}"


def test_artifact_contract_defines_paths_manifest_and_trace() -> None:
    text = read("references/artifact-contract.md")
    for phrase in (
        "manifest.json",
        "artifacts/raw/<source-id>/",
        "artifacts/normalized/<work-package-id>/",
        "artifacts/derived/<work-package-id>/",
        "artifacts/scripts/<work-package-id>/",
        "schema_version",
        '"inputs"',
        "adopted",
        "superseded",
        "failed",
        "not_adopted",
        "report.md",
        "W10",
        "evidence.md",
        "报告章节、关键主张定位、owner 工作包、证据定位和采用状态",
    ):
        assert phrase in text


def test_w10_declares_the_same_mapping_table_fields() -> None:
    text = read("references/work-packages/core/W10-report-review.md")
    assert "报告章节、关键主张定位、owner 工作包、证据定位和采用状态" in text


SCRIPT_RETENTION_PROMPT = (
    "> **保留中间脚本：** 本次运行用于二期质量加固实测。凡为生成报告而创建或修改的采集、解析、\n"
    "> 清洗、计算、校验、修复或补采脚本，均须把实际执行版本和失败版本保存到\n"
    "> `artifacts/scripts/<work-package-id>/`，并在 `manifest.json` 登记用途、安全调用方式、"
    "输入输出、\n"
    "> 环境与依赖、退出状态和哈希；不得删除、静默覆盖或只保留最终数据。保留不代表批准接入\n"
    "> canonical Skill。"
)


def _normalize_verbatim(text: str) -> str:
    return " ".join(re.sub(r"(?m)^>\s?", "", text).replace("**", " ").split())


def test_w0_contains_complete_script_retention_prompt() -> None:
    text = read("references/work-packages/core/W0-task-framing.md")
    assert _normalize_verbatim(SCRIPT_RETENTION_PROMPT) in _normalize_verbatim(text)
    assert "报告记录实际分析模型" in text


def test_artifact_contract_carries_the_same_script_retention_prompt() -> None:
    text = read("references/artifact-contract.md")
    assert _normalize_verbatim(SCRIPT_RETENTION_PROMPT) in _normalize_verbatim(text)


@pytest.mark.parametrize("work_package_id", tuple(f"W{index}" for index in range(11)))
def test_each_core_work_package_declares_report_ownership(work_package_id: str) -> None:
    path = next(ROOT.glob(f"references/work-packages/core/{work_package_id}-*.md"))
    text = path.read_text(encoding="utf-8")
    assert "## 二期结果归属" in text
    assert "报告章节" in text


REPORT_OWNERSHIP = {
    "W0": "1. 任务与时点",
    "W1": "1、3、8、11",
    "W2": "4、8、9、11",
    "W3": "3、9、11",
    "W4": "3、4、9、11",
    "W5": "5、9、11",
    "W6": "6、9、11",
    "W7": "7、9、11",
    "W8": "8、9、11",
    "W9": "2、9、10、12",
    "W10": "汇总全部十二章，不新增上游事实",
}


@pytest.mark.parametrize(("work_package_id", "ownership"), REPORT_OWNERSHIP.items())
def test_work_package_report_ownership_is_explicit(
    work_package_id: str, ownership: str
) -> None:
    path = next(ROOT.glob(f"references/work-packages/core/{work_package_id}-*.md"))
    text = path.read_text(encoding="utf-8")
    assert ownership in text
    assert f"work-packages/{path.name}" in text
