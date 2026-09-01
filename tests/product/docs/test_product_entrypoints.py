"""Product entry-point document claims.

These tests read the user-facing documents (README, agent-skill usage guide,
CHANGELOG) and assert mechanically that:

* old-normal-workflow markers are gone (``run init``, ``run submit``,
  ``run resume``, ``report render`` as current CLI commands, ``S0–S9`` as a
  *current* product feature, and any claim that the CLI validates research
  semantics);
* the Agent-execution claims are present (natural-language start, canonical
  Skill owns the workflow, ``skill``/``helper`` role split with ``legacy``
  described only as removed history, helper-unavailable continuation,
  authorized-failure semantics, host statuses, a single current unreleased
  V0.2 entry, and the retained V0.1 internal baseline in CHANGELOG).

Command-style matches use backtick- or ``hetu-stock``-prefixed patterns so a
legitimate substring like ``legacy run show`` does not false-positive on
``run``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "agent-skill-usage.md"
CHANGELOG = ROOT / "CHANGELOG.md"
CANONICAL_SKILL = ROOT / "skills" / "hetu-stock-analysis" / "SKILL.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_entrypoints_share_the_same_subject_contract() -> None:
    current_documents = {
        "README": _read(README),
        "usage": _read(USAGE),
    }
    for name, text in current_documents.items():
        assert "明确公司名称" in text, f"{name} omits company-name input"
        assert "6 位" in text or "6位" in text, f"{name} omits code input"
        assert all(suffix in text for suffix in (".SH", ".SZ", ".BJ"))

    skill = _read(CANONICAL_SKILL)
    assert "明确公司名称" in skill
    assert "有后缀或无后缀代码" in skill
    assert "subject.ref 必须是 6 位数字" not in current_documents["README"]
    assert "自由文本会被拒绝" not in current_documents["usage"]


def test_current_usage_does_not_describe_v01_uninstall_as_current() -> None:
    usage = _read(USAGE)
    assert "V0.1 不提供自动卸载" not in usage
    assert "当前安装器不提供自动卸载命令" in usage
    assert "安装器不会删除非受管文件" in usage


# Patterns that flag a forbidden *current* normal command. Each is anchored to
# a command-ish context (backticked command, or ``hetu-stock <verb>``) so that
# historical mentions inside prose or ``legacy run show`` are not flagged.
_FORBIDDEN_COMMAND_PATTERNS = (
    re.compile(r"`hetu-stock run init`"),
    re.compile(r"`hetu-stock run submit`"),
    re.compile(r"`hetu-stock run resume`"),
    re.compile(r"`hetu-stock report render`"),
    re.compile(r"`run init`"),
    re.compile(r"`run submit`"),
    re.compile(r"`run resume`"),
    re.compile(r"`report render`"),
    re.compile(r"hetu-stock run init\b"),
    re.compile(r"hetu-stock run submit\b"),
    re.compile(r"hetu-stock run resume\b"),
    re.compile(r"hetu-stock report render\b"),
)


def _assert_no_forbidden_commands(text: str, where: str) -> None:
    for pattern in _FORBIDDEN_COMMAND_PATTERNS:
        assert pattern.search(text) is None, (
            f"{where}: forbidden current command marker matched by {pattern.pattern}"
        )


def test_readme_has_no_old_normal_workflow_commands() -> None:
    _assert_no_forbidden_commands(_read(README), "README.md")


def test_usage_guide_has_no_old_normal_workflow_commands() -> None:
    _assert_no_forbidden_commands(_read(USAGE), "docs/agent-skill-usage.md")


def test_readme_rejects_s0_s9_presented_as_current_feature() -> None:
    readme = _read(README)
    # The historical V0.1 feature block was removed with the version reset;
    # the S0-S9 pipeline must not appear anywhere in the README.
    assert "S0–S9" not in readme, (
        "README must not advertise the retired S0–S9 pipeline"
    )


def test_readme_does_not_claim_cli_validates_research_semantics() -> None:
    readme = _read(README)
    assert "CLI 负责确定性校验" not in readme, (
        "README must not claim the CLI is the deterministic validation path "
        "for research semantics"
    )


def test_readme_requires_natural_language_start() -> None:
    readme = _read(README)
    # Users start by saying a natural-language request to a host Agent.
    assert "自然语言" in readme
    assert ("Codex" in readme or "OpenCode" in readme)


def test_readme_states_canonical_skill_owns_the_workflow() -> None:
    readme = _read(README)
    assert "canonical Skill" in readme


def test_readme_states_skill_helper_role_split() -> None:
    readme = _read(README)
    assert "hetu-stock skill" in readme
    assert "hetu-stock helper" in readme
    assert "可选" in readme or "optional" in readme.lower()


def test_readme_describes_legacy_only_as_removed_history() -> None:
    readme = _read(README)
    # The Phase-1 legacy surface is removed history, never an available group.
    assert "legacy 兼容面已退场" in readme
    assert "三期 C3" not in readme
    assert "hetu-stock legacy`：开发者专用的只读历史命令面" not in readme
    assert "只暴露三个顶层组" not in readme
    assert "只暴露两个顶层组" in readme


def test_readme_states_helper_unavailable_continuation() -> None:
    readme = _read(README)
    # public research continues with host equivalents when helper unavailable
    assert "公开" in readme
    assert "等价" in readme or "宿主" in readme


def test_readme_states_authorized_failure_semantics() -> None:
    readme = _read(README)
    # The spec claim: authorized failure blocks only related data, stays
    # authorized until an explicit user decision, and never persists a
    # resolved-secret list. Assert the three distinctive contiguous phrases
    # the README actually uses in its 授权失败语义 section, so the test fails
    # if that paragraph is deleted or watered down.
    assert "只阻塞与该来源相关的数据" in readme
    assert "运行保持 authorized 状态，直到用户做出显式决定" in readme
    assert "从不持久化已解析的 secret 列表" in readme


def _assert_phase2_host_statuses(text: str, where: str) -> None:
    """Assert completion does not promote any host certification."""
    all_unverified = re.compile(
        r"Codex\s*(?:、|与)\s*OpenCode\s*(?:、|与)\s*Claude Code"
        r"[^。\n]*`UNVERIFIED`"
    )
    assert all_unverified.search(text) is not None, (
        f"{where}: Codex, OpenCode and Claude Code must remain UNVERIFIED"
    )
    for host in ("Codex", "OpenCode", "Claude Code"):
        forbidden = (
            rf"\|\s*{re.escape(host)}\s*\|\s*`?PASS`?\s*\|",
            rf"{re.escape(host)}\s*(?::|：|当前状态为|认证状态为)\s*`?PASS`?",
            rf"{re.escape(host)}[^。\n]{{0,24}}(?:已正式支持|已通过正式宿主认证)",
        )
        assert not any(re.search(pattern, text, re.IGNORECASE) for pattern in forbidden), (
            f"{where}: {host} must not be presented as PASS or formally supported"
        )


def test_phase2_host_status_assertion_rejects_swapped_mappings() -> None:
    swapped_statuses = (
        "Codex、OpenCode 与 Claude Code 的认证状态均为 `UNVERIFIED`。\n"
        "| Codex | PASS | 已支持 |"
    )

    with pytest.raises(AssertionError):
        _assert_phase2_host_statuses(swapped_statuses, "swapped fixture")


def test_readme_states_host_statuses() -> None:
    readme = _read(README)
    _assert_phase2_host_statuses(readme, "README.md")


def test_usage_guide_states_canonical_skill_owns_workflow() -> None:
    usage = _read(USAGE)
    assert "canonical Skill" in usage
    assert "请求理解" in usage
    assert "研究规划" in usage
    assert "失败处理" in usage
    assert "综合" in usage


def test_usage_guide_states_skill_helper_role_split() -> None:
    usage = _read(USAGE)
    assert "hetu-stock skill" in usage
    assert "hetu-stock helper" in usage


def test_usage_guide_describes_legacy_only_as_removed_history() -> None:
    usage = _read(USAGE)
    assert "legacy 兼容面已退场" in usage
    assert "hetu-stock legacy`：开发者专用的只读历史命令面" not in usage


def test_usage_guide_describes_report_guidance_without_an_old_template() -> None:
    usage = _read(USAGE)
    assert "报告模板" not in usage
    assert "报告撰写指引" in usage


def test_usage_guide_states_helper_unavailable_continuation() -> None:
    usage = _read(USAGE)
    assert "公开" in usage
    assert "等价" in usage or "宿主" in usage


def test_usage_guide_states_authorized_failure_semantics() -> None:
    usage = _read(USAGE)
    # Same three-part spec claim as README; assert the distinctive contiguous
    # phrases the usage guide actually uses in its authorized 模式 section.
    assert "只阻塞与该来源相关的数据" in usage
    assert "运行保持 authorized 状态，直到用户做出显式决定" in usage
    assert "系统从不持久化已解析的 secret 列表" in usage


def test_usage_guide_states_host_support_statuses() -> None:
    usage = _read(USAGE)
    _assert_phase2_host_statuses(usage, "docs/agent-skill-usage.md")
    assert "安装兼容性本身不等同于支持" in usage
    assert "正式支持" in usage
    assert "完整证据" in usage


def test_usage_guide_does_not_claim_cli_validates_research_semantics() -> None:
    usage = _read(USAGE)
    assert "CLI 负责确定性校验" not in usage
    assert "请求规范化、状态迁移校验、证据元数据检查、门禁评估与报告校验" not in usage


def test_changelog_records_current_unreleased_v02_and_v01_baseline() -> None:
    text = _read(CHANGELOG)
    assert text.count("## V0.2（未正式发布）") == 1
    assert "## V0.1（内部基线，未正式发布）" in text
    # Only implemented, delivered functionality belongs in the changelog.
    assert "V0.3" not in text
    assert "后续规划" not in text
    assert "二期加固负责" not in text
    assert "长期需求负责" not in text
    # Released claims stay bounded: no release, no host certification.
    v02_start = text.index("## V0.2（未正式发布）")
    v02_section = text[v02_start:]
    next_header = re.search(r"\n## V0\.", v02_section[1:])
    v02_body = (
        v02_section if next_header is None else v02_section[: next_header.start() + 1]
    )
    assert not re.search(r"(?:Codex|OpenCode|Claude Code)[^。\n]{0,24}`?PASS`?", v02_body)
    assert "已通过正式宿主认证" not in v02_body
    assert "正式宿主支持认证仍为 `UNVERIFIED`" in v02_body


def test_install_script_uses_new_helper_wording() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "Installing optional deterministic helpers and Skill management CLI" in installer
    assert "Installing the Python helper..." not in installer


def test_install_script_self_check_unchanged() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "skill validate" in installer
    assert "skill install" in installer
    assert '"$LAUNCHER" --help' in installer
