from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[3] / "scripts" / "check_docs.py"
REPO_ROOT = Path(__file__).resolve().parents[3]
HOST_ACCEPTANCE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "host-acceptance.yml"


def test_host_acceptance_workflow_is_manual_only_and_secret_backed() -> None:
    workflow = HOST_ACCEPTANCE_WORKFLOW.read_text(encoding="utf-8")
    # 真实验收只接显式手动触发，不接 push/PR 默认自动执行
    assert "workflow_dispatch" in workflow
    assert re.search(r"^\s*push:", workflow, re.MULTILINE) is None
    assert "pull_request" not in workflow
    # 凭据只经 GitHub Secrets 引用，仓库内不出现明文；case 清单留占位说明
    assert "secrets." in workflow
    assert "仅已批准 case" in workflow
    # 受约束观察输入：claude 探针 / zcode attach 规格
    assert re.search(r"^\s+probe:", workflow, re.MULTILINE) is not None
    assert re.search(r"^\s+watch_specs:", workflow, re.MULTILINE) is not None


def test_host_acceptance_workflow_run_step_injects_inputs_via_env() -> None:
    workflow = HOST_ACCEPTANCE_WORKFLOW.read_text(encoding="utf-8")
    # step 级 env 不跨步骤：run 步骤必须自行注入全部输入
    run_step = workflow.split("name: Run single approved case", 1)[1]
    run_step = run_step.split("- name:", 1)[0]
    assert "AUTHORIZATION_REF: ${{ inputs.authorization_ref }}" in run_step
    assert "PROBE: ${{ inputs.probe }}" in run_step
    assert "WATCH_SPECS: ${{ inputs.watch_specs }}" in run_step
    assert '--authorization-ref "$AUTHORIZATION_REF"' in run_step
    assert "--probe" in run_step
    assert '--watch-agent "$spec"' in run_step
    # 宿主与观察模式不匹配时明确失败，不接任意 shell
    assert "未执行" in run_step


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tracked_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    for relative, text in files.items():
        _write(root, relative, text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return root


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _valid_current_documents() -> dict[str, str]:
    return {
        "README.md": "# Project\n\n[Guide](docs/agent-skill-usage.md)\n",
        "docs/agent-skill-usage.md": "# Usage\n\n[Contract](../specs/contract.md)\n",
        "docs/archive.md": "# Archive\n\n`hetu-stock run submit` was removed.\n",
        "specs/contract.md": "# Historical requirement\n\n`run resume` was supported.\n",
        "specs/plans/old.md": "# Historical plan\n\n`report render` was planned.\n",
        "skills/hetu-stock-analysis/SKILL.md": (
            "# Canonical Skill\n\n"
            "[Rules](references/rules.md)\n\n"
            "## Legacy example\n\n"
            "The historical command was `run init`.\n"
        ),
        "skills/hetu-stock-analysis/references/rules.md": "# Rules\n",
        "CHANGELOG.md": (
            "# Changelog\n\n"
            "## V0.2 (unreleased)\n\nCurrent Agent workflow.\n\n"
            "## V0.1\n\n`hetu-stock report render` was supported.\n"
        ),
    }


def test_document_checker_accepts_valid_tracked_links(tmp_path: Path) -> None:
    root = _tracked_tree(tmp_path, _valid_current_documents())

    completed = _run_checker(root)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Markdown links: PASS" in completed.stdout


def test_document_checker_rejects_broken_tracked_link(tmp_path: Path) -> None:
    files = _valid_current_documents()
    files["README.md"] = "# Project\n\n[Missing](docs/missing.md)\n"
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "README.md" in completed.stdout
    assert "docs/missing.md" in completed.stdout


def test_document_checker_skips_hetu_links_only_without_runtime_tree(
    tmp_path: Path,
) -> None:
    # Clean clones have no .hetu runtime tree: links into it cannot be
    # verified and must not gate.
    files = _valid_current_documents()
    files["specs/design.md"] = (
        "# Design\n\n[Evidence](../.hetu/research/002371-run/report.md)\n"
    )
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode == 0, completed.stdout


def test_document_checker_verifies_hetu_links_when_runtime_tree_exists(
    tmp_path: Path,
) -> None:
    # With the runtime tree present, a misspelled evidence link fails the
    # gate like any other broken relative link.
    files = _valid_current_documents()
    files["specs/design.md"] = (
        "# Design\n\n[Evidence](../.hetu/research/002371-run/report.md)\n"
    )
    root = _tracked_tree(tmp_path, files)
    (root / ".hetu" / "research" / "002371-run").mkdir(parents=True)
    (root / ".hetu" / "research" / "002371-run" / "report.md").write_text(
        "# Report\n", encoding="utf-8"
    )
    _write(
        root,
        "specs/design.md",
        "# Design\n\n[Evidence](../.hetu/research/002371-run/repotr.md)\n",
    )

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "repotr.md" in completed.stdout


def test_document_checker_rejects_relative_link_outside_repository(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    files = _valid_current_documents()
    files["README.md"] = "# Project\n\n[Escape](../outside.md)\n"
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "README.md" in completed.stdout
    assert "../outside.md" in completed.stdout


def test_document_checker_rejects_symlink_link_outside_repository(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    files = _valid_current_documents()
    files["README.md"] = "# Project\n\n[Escape](docs/escape.txt)\n"
    root = _tracked_tree(tmp_path, files)
    link = root / "docs/escape.txt"
    link.symlink_to(outside)
    subprocess.run(["git", "-C", str(root), "add", "docs/escape.txt"], check=True)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "README.md" in completed.stdout
    assert "docs/escape.txt" in completed.stdout


def test_document_checker_rejects_symlink_loop_without_traceback(
    tmp_path: Path,
) -> None:
    files = _valid_current_documents()
    files["README.md"] = "# Project\n\n[Loop](docs/loop.txt)\n"
    root = _tracked_tree(tmp_path, files)
    link = root / "docs/loop.txt"
    link.symlink_to(link.name)
    subprocess.run(["git", "-C", str(root), "add", "docs/loop.txt"], check=True)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "README.md" in completed.stdout
    assert "docs/loop.txt" in completed.stdout
    assert "unresolved" in completed.stdout
    assert "Traceback" not in completed.stderr


def test_document_checker_rejects_null_byte_link_without_traceback(
    tmp_path: Path,
) -> None:
    files = _valid_current_documents()
    files["README.md"] = "# Project\n\n[Invalid](docs/%00escape.txt)\n"
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "README.md" in completed.stdout
    assert "docs/%00escape.txt" in completed.stdout
    assert "unresolved" in completed.stdout
    assert "Traceback" not in completed.stderr


def test_document_checker_accepts_historical_obsolete_commands(tmp_path: Path) -> None:
    root = _tracked_tree(tmp_path, _valid_current_documents())

    completed = _run_checker(root)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Obsolete commands: PASS" in completed.stdout


def test_document_checker_rejects_current_obsolete_command(tmp_path: Path) -> None:
    files = _valid_current_documents()
    files["skills/hetu-stock-analysis/SKILL.md"] = (
        "# Canonical Skill\n\nRun `hetu-stock run init` to begin.\n"
    )
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "skills/hetu-stock-analysis/SKILL.md" in completed.stdout
    assert "hetu-stock run init" in completed.stdout


@pytest.mark.parametrize(
    "heading",
    [
        "Legacy example",
        "Historical example",
        "历史示例",
        "Legacy example: V0.1",
        "Historical example：V0.1",
        "历史示例: 一期命令",
    ],
)
def test_document_checker_accepts_explicit_historical_example_sections(
    tmp_path: Path,
    heading: str,
) -> None:
    files = _valid_current_documents()
    files["skills/hetu-stock-analysis/SKILL.md"] = (
        f"# Canonical Skill\n\n## {heading}\n\n"
        "```bash\nrun init\nreport render\n```\n"
    )
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("skill_text", "obsolete_command"),
    [
        (
            "# Canonical Skill\n\n## Non-legacy quick start\n\n`run init`\n",
            "run init",
        ),
        (
            "# Canonical Skill\n\n## 历史命令禁用说明\n\n`run submit`\n",
            "run submit",
        ),
        (
            "# Canonical Skill\n\n```text\n## historical\n```\n\n`run resume`\n",
            "run resume",
        ),
        (
            "# Canonical Skill\n\n## Legacy examples are forbidden\n\n`run init`\n",
            "run init",
        ),
        (
            "# Canonical Skill\n\n## 历史示例禁用说明\n\n`run submit`\n",
            "run submit",
        ),
        (
            "# Canonical Skill\n\n## Legacy example forbidden\n\n`run resume`\n",
            "run resume",
        ),
        (
            "# Canonical Skill\n\n## Historical example-forbidden\n\n`report render`\n",
            "report render",
        ),
    ],
    ids=(
        "negated-legacy",
        "current-history-status",
        "fenced-fake-heading",
        "plural-label",
        "chinese-unseparated-suffix",
        "ascii-unseparated-suffix",
        "hyphenated-suffix",
    ),
)
def test_document_checker_rejects_non_example_history_headings(
    tmp_path: Path,
    skill_text: str,
    obsolete_command: str,
) -> None:
    files = _valid_current_documents()
    files["skills/hetu-stock-analysis/SKILL.md"] = skill_text
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert obsolete_command in completed.stdout


def test_document_checker_rejects_bare_commands_in_fenced_code(tmp_path: Path) -> None:
    files = _valid_current_documents()
    files["README.md"] = (
        "# Project\n\n## Quick start\n\n"
        "```bash\nrun init\nreport render\n```\n"
    )
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "run init" in completed.stdout
    assert "report render" in completed.stdout


def test_document_checker_rejects_bare_command_in_inline_code(tmp_path: Path) -> None:
    files = _valid_current_documents()
    files["docs/agent-skill-usage.md"] = "# Usage\n\nRun `run resume` now.\n"
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "run resume" in completed.stdout


def test_changelog_v02_failure_uses_original_line_number(tmp_path: Path) -> None:
    files = _valid_current_documents()
    files["CHANGELOG.md"] = (
        "# Changelog\n"
        "\n"
        "Release history.\n"
        "\n"
        "## V0.2 (unreleased)\n"
        "\n"
        "```bash\n"
        "run init\n"
        "```\n"
        "\n"
        "## V0.1\n"
        "\n"
        "Historical release.\n"
    )
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert "CHANGELOG.md:8: obsolete command run init" in completed.stdout


@pytest.mark.parametrize(
    ("physical_command", "obsolete_command"),
    (
        ("run \\\n  init", "run init"),
        ("run \\\n  submit", "run submit"),
        ("run \\\n  resume", "run resume"),
        ("report \\\n  render", "report render"),
    ),
)
@pytest.mark.parametrize("block", ("fenced", "indented"))
def test_document_checker_rejects_shell_continued_obsolete_commands(
    tmp_path: Path,
    physical_command: str,
    obsolete_command: str,
    block: str,
) -> None:
    files = _valid_current_documents()
    if block == "fenced":
        files["README.md"] = f"# Project\n\n```bash\n{physical_command}\n```\n"
        first_line = 4
    else:
        indented = physical_command.replace("\n", "\n    ")
        files["README.md"] = f"# Project\n\n    {indented}\n"
        first_line = 3
    root = _tracked_tree(tmp_path, files)

    completed = _run_checker(root)

    assert completed.returncode != 0
    assert (
        f"README.md:{first_line}: obsolete command {obsolete_command}"
        in completed.stdout
    )
