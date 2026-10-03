from pathlib import Path

import pytest

from hetu_stock.skill import SkillValidationError, validate_skill_package
from tests.product.skill.contract_fixtures import build_contract_fixture

FRONTMATTER = "---\nname: hetu-stock-analysis\ndescription: synthetic contract\n---\n"


def _contract_root(tmp_path: Path, body: str = "") -> Path:
    root, _ = build_contract_fixture(tmp_path)
    root.joinpath("SKILL.md").write_text(FRONTMATTER + body, encoding="utf-8")
    return root


def test_canonical_skill_package_is_valid() -> None:
    validate_skill_package(Path("skills/hetu-stock-analysis"), require_manifest=True)


def test_deployable_skill_package_requires_manifest(tmp_path: Path) -> None:
    root = _contract_root(tmp_path)
    with pytest.raises(SkillValidationError, match="MANIFEST.json is required"):
        validate_skill_package(root, require_manifest=True)


def test_deployable_contract_checks_manifest_coverage(tmp_path: Path) -> None:
    root = _contract_root(tmp_path)
    root.joinpath("MANIFEST.json").write_text('{"files": {}}\n', encoding="utf-8")
    with pytest.raises(SkillValidationError, match="manifest coverage missing"):
        validate_skill_package(root, require_manifest=True)


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("{not json\n", "MANIFEST.json is invalid"),
        ("[]\n", "MANIFEST.json must contain a 'files' object"),
        ("{}\n", "MANIFEST.json must contain a 'files' object"),
        ('{"files": []}\n', "MANIFEST.json 'files' must be a mapping"),
    ],
)
def test_malformed_manifest_is_normalized_to_skill_validation_error(
    tmp_path: Path, manifest: str, message: str
) -> None:
    root = _contract_root(tmp_path)
    root.joinpath("MANIFEST.json").write_text(manifest, encoding="utf-8")

    with pytest.raises(SkillValidationError, match=message):
        validate_skill_package(root, require_manifest=True)


def test_skill_requires_named_reference(tmp_path: Path) -> None:
    root = _contract_root(
        tmp_path,
        "See [references/missing.md](references/missing.md)\n",
    )
    with pytest.raises(SkillValidationError, match="references/missing.md"):
        validate_skill_package(root)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\nname: hetu-stock-analysis\n", "frontmatter"),
        ("---\nname: other-skill\ndescription: x\n---\n", "name"),
        ("---\nname: hetu-stock-analysis\n---\n", "fields"),
        ("---\nname: hetu-stock-analysis\ndescription: ''\n---\n", "description"),
        (
            "---\nname: hetu-stock-analysis\ndescription: x\nmetadata: {}\n---\n",
            "fields",
        ),
    ],
)
def test_invalid_frontmatter_is_rejected(
    tmp_path: Path, text: str, message: str
) -> None:
    root, _ = build_contract_fixture(tmp_path)
    root.joinpath("SKILL.md").write_text(text, encoding="utf-8")
    with pytest.raises(SkillValidationError, match=message):
        validate_skill_package(root)


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("../outside.md", "outside the skill"),
        ("/etc/passwd", "absolute paths are not allowed"),
        ("file:///etc/passwd", "file:"),
        ("C:/Windows/System32", "C:"),
        (r"references\\diagram.png", "backslash"),
        ("C%3A/Windows/System32", "Windows"),
        ("%43:/Windows/System32", "Windows"),
        ("C%3Afoo.md", "Windows"),
    ],
)
def test_unsafe_local_reference_is_rejected(
    tmp_path: Path, target: str, message: str
) -> None:
    root = _contract_root(tmp_path, f"[link]({target})\n")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    root.joinpath("C:").mkdir(exist_ok=True)
    root.joinpath("C:", "Windows").mkdir(exist_ok=True)
    root.joinpath("C:", "Windows", "System32").write_text("x", encoding="utf-8")
    root.joinpath("C:foo.md").write_text("x", encoding="utf-8")
    with pytest.raises(SkillValidationError, match=message):
        validate_skill_package(root)


@pytest.mark.parametrize(
    "body",
    [
        "Contact [ops](mailto:ops@example.com)\n",
        "[site](//example.com/a)\n",
        "```markdown\n[ignored](references/missing.md)\n```\n",
        '---not-frontmatter---\n',
    ],
)
def test_nonlocal_or_code_block_links_are_accepted(tmp_path: Path, body: str) -> None:
    root = _contract_root(tmp_path, body)
    validate_skill_package(root)


@pytest.mark.parametrize(
    "body",
    [
        "[link](references/architecture.md#section)\n",
        '[link](references/architecture.md "Architecture")\n',
        "[link text][arch]\n\n[arch]: references/architecture.md\n",
        "![diagram](references/architecture.md)\n",
        "[link](references/architecture.md?raw=1)\n",
    ],
)
def test_existing_local_link_forms_are_accepted(tmp_path: Path, body: str) -> None:
    root = _contract_root(tmp_path, body)
    root.joinpath("references", "architecture.md").write_text("arch", encoding="utf-8")
    validate_skill_package(root)


def test_reference_style_missing_file_is_rejected(tmp_path: Path) -> None:
    root = _contract_root(
        tmp_path,
        "[link text][arch]\n\n[arch]: references/missing.md\n",
    )
    with pytest.raises(SkillValidationError, match="references/missing.md"):
        validate_skill_package(root)


def test_link_in_frontmatter_is_ignored(tmp_path: Path) -> None:
    root, _ = build_contract_fixture(tmp_path)
    root.joinpath("SKILL.md").write_text(
        '---\nname: hetu-stock-analysis\ndescription: "See [guide](missing.md)"\n---\n',
        encoding="utf-8",
    )
    validate_skill_package(root)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks not supported")
def test_symlinked_skill_file_outside_root_is_rejected(tmp_path: Path) -> None:
    root = _contract_root(tmp_path)
    root.joinpath("SKILL.md").unlink()
    outside = tmp_path / "outside-skill.md"
    outside.write_text(FRONTMATTER, encoding="utf-8")
    root.joinpath("SKILL.md").symlink_to(outside)
    with pytest.raises(SkillValidationError, match="SKILL.md"):
        validate_skill_package(root)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks not supported")
def test_symlinked_reference_outside_root_is_rejected(tmp_path: Path) -> None:
    root = _contract_root(tmp_path, "[link](ref.md)\n")
    outside = tmp_path / "outside-reference.md"
    outside.write_text("x", encoding="utf-8")
    root.joinpath("ref.md").symlink_to(outside)
    with pytest.raises(SkillValidationError, match="outside the skill"):
        validate_skill_package(root)


def test_forbidden_phase_one_path_is_rejected(tmp_path: Path) -> None:
    root = _contract_root(tmp_path)
    root.joinpath("references", "workflow.md").write_text("legacy", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="forbidden phase-one path"):
        validate_skill_package(root)


def test_work_package_value_error_message_is_preserved_at_public_boundary(
    tmp_path: Path,
) -> None:
    root = _contract_root(tmp_path)
    victim = next(root.glob("references/work-packages/core/W0-*.md"))
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("start_requires: []", "start_requires: [W99]"),
        encoding="utf-8",
    )
    with pytest.raises(SkillValidationError, match="unknown dependency target: W99"):
        validate_skill_package(root)


# 阶段 07.1b：包清单必须覆盖实际宿主元数据。元数据只映射名称／描述／触发提示，
# 不增加研究指令；MANIFEST.json 必须按哈希覆盖该文件。
SKILL_ROOT = Path("skills/hetu-stock-analysis")
HOST_METADATA_PATH = SKILL_ROOT / "hosts.json"
KNOWN_HOSTS = ("codex", "claude", "opencode", "zcode")
ALLOWED_HOST_METADATA_FIELDS = {"name", "description", "trigger"}


def test_host_metadata_maps_only_name_description_trigger() -> None:
    import json
    import re

    payload = json.loads(HOST_METADATA_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert set(payload["hosts"]) == set(KNOWN_HOSTS)
    forbidden = re.compile(r"\bW(?:10|[0-9])\b|工作包|orchestration")
    for host in KNOWN_HOSTS:
        metadata = payload["hosts"][host]
        assert set(metadata) == ALLOWED_HOST_METADATA_FIELDS, (
            f"{host}: 宿主元数据字段只允许 name/description/trigger"
        )
        for field, value in metadata.items():
            assert isinstance(value, str) and value.strip(), (
                f"{host}.{field}: 必须是 非空字符串"
            )
            assert not forbidden.search(value), (
                f"{host}.{field}: 宿主元数据不得包含研究指令内容"
            )


def test_manifest_covers_host_metadata_by_hash() -> None:
    import hashlib
    import json

    manifest = json.loads(
        (SKILL_ROOT / "MANIFEST.json").read_text(encoding="utf-8")
    )
    relative = "hosts.json"
    assert relative in manifest["files"], "MANIFEST.json 必须覆盖宿主元数据 hosts.json"
    digest = hashlib.sha256()
    digest.update(HOST_METADATA_PATH.read_bytes())
    assert manifest["files"][relative] == digest.hexdigest()
