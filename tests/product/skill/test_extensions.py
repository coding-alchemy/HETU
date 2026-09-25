"""Phase 5 stage 06.1: extension validation, atomic registry, host lifecycle.

Behavior under test (design §4.1–4.2): install only saves and validates a
candidate and stays disabled by default; enablement is per host; published
versions are read-only; updates prepare a new candidate; rollback and
uninstall never break other hosts; hard dependency failures close the
candidate; scripts, hooks, undeclared files, unsafe paths and out-of-scope
capabilities are refused without executing anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hetu_stock.skill.extensions import (
    ExtensionError,
    context_for_host,
    disable_extension,
    enable_extension,
    inspect_extension,
    install_extension,
    list_extensions,
    uninstall_extension,
    update_extension,
    validate_extension,
)
from tests.product.skill.extension_fixtures import build_extension_package

HOSTS = ("codex", "claude", "opencode", "zcode")


@pytest.fixture(autouse=True)
def _isolated_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """5-B1：全部用例（含 validate 路径）都与调用者机器的真实登记表隔离。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _registry(root: Path) -> dict:
    return json.loads((root / "extensions" / "registry.json").read_text(encoding="utf-8"))


def _write_fake_registry(data_home: Path, registry: dict) -> None:
    root = data_home / "hetu-stock" / "extensions"
    root.mkdir(parents=True, exist_ok=True)
    (root / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- validation ---------------------------------------------------------------


def test_validate_extension_returns_checked_summary(tmp_path: Path) -> None:
    source = build_extension_package(tmp_path / "candidate")
    result = validate_extension(source)
    assert result["id"] == "x.demo.rules"
    assert result["version"] == "0.1.0"
    assert result["capabilities"] == ["local-files-read"]
    assert set(result["files"]) == {
        "extension.json",
        "work-packages/x.demo.rules.md",
    }
    assert result["files"]["extension.json"]


def test_validate_rejects_core_id_conflict(tmp_path: Path) -> None:
    source = build_extension_package(tmp_path / "candidate", extension_id="W0")
    with pytest.raises(ExtensionError, match="ID"):
        validate_extension(source)


def test_validate_rejects_wx_official_id_conflict(tmp_path: Path) -> None:
    source = build_extension_package(tmp_path / "candidate", extension_id="WX.extra")
    with pytest.raises(ExtensionError, match="ID"):
        validate_extension(source)


def test_validate_rejects_malformed_id(tmp_path: Path) -> None:
    source = build_extension_package(tmp_path / "candidate", extension_id="demo.rules")
    with pytest.raises(ExtensionError, match="ID"):
        validate_extension(source)


def test_validate_rejects_missing_hard_dependency(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate",
        package_ids=(("x.demo.rules", ("x.demo.missing",), (), ()),),
    )
    with pytest.raises(ExtensionError, match="依赖|dependency"):
        validate_extension(source)


def test_validate_dependency_resolution_reads_isolated_registry_in_both_states(
    tmp_path: Path,
) -> None:
    """5-B1：同一假登记表的正反两态决定依赖判断，与真实登记表无关。"""
    source = build_extension_package(
        tmp_path / "candidate",
        package_ids=(("x.demo.rules", ("x.demo.missing",), (), ()),),
    )
    data_home = tmp_path / "data"
    _write_fake_registry(
        data_home, {"extensions": {}, "bindings": {host: {} for host in HOSTS}}
    )
    with pytest.raises(ExtensionError, match="依赖|dependency"):
        validate_extension(source)

    _write_fake_registry(
        data_home,
        {
            "extensions": {
                "x.demo.provider": {
                    "versions": {"0.1.0": {"provides": ["x.demo.missing"]}}
                }
            },
            "bindings": {host: {} for host in HOSTS},
        },
    )
    result = validate_extension(source)
    assert result["id"] == "x.demo.rules"
    assert result["requires"] == ["x.demo.missing"]


def test_validate_rejects_hard_dependency_cycle(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate",
        package_ids=(
            ("x.demo.a", ("x.demo.b",), (), ()),
            ("x.demo.b", ("x.demo.a",), (), ()),
        ),
    )
    with pytest.raises(ExtensionError, match="环|cycle"):
        validate_extension(source)


def test_validate_allows_revisit_only_loop(tmp_path: Path) -> None:
    """纯回访关系不是硬依赖：may_reopen 环必须通过校验。"""
    source = build_extension_package(
        tmp_path / "candidate",
        package_ids=(
            ("x.demo.a", (), (), ("x.demo.b",)),
            ("x.demo.b", (), (), ("x.demo.a",)),
        ),
    )
    result = validate_extension(source)
    assert result["id"] == "x.demo.rules"


def test_validate_rejects_broken_summary(tmp_path: Path) -> None:
    source = build_extension_package(tmp_path / "candidate", summary=42)
    with pytest.raises(ExtensionError, match="摘要|summary"):
        validate_extension(source)


def test_validate_rejects_out_of_scope_capability(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate", capabilities=["local-files-read", "execute-code"]
    )
    with pytest.raises(ExtensionError, match="权限|capabilit"):
        validate_extension(source)


def test_validate_rejects_script_files_without_executing(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate",
        references=("references/run.py",),
    )
    marker = tmp_path / "executed.marker"
    body = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n"
    (source / "references" / "run.py").write_text(body, encoding="utf-8")
    with pytest.raises(ExtensionError, match="脚本|script|代码"):
        validate_extension(source)
    assert not marker.exists(), "附带代码绝不能被执行"


def test_validate_rejects_undeclared_files(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate", extra_files=("notes/secret.md",)
    )
    with pytest.raises(ExtensionError, match="未声明|declared"):
        validate_extension(source)


def test_validate_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("user data\n", encoding="utf-8")
    source = build_extension_package(
        tmp_path / "candidate", references=("references/linked.md",)
    )
    linked = source / "references" / "linked.md"
    linked.unlink()
    linked.symlink_to(outside)
    with pytest.raises(ExtensionError, match="符号链接|symlink|越界"):
        validate_extension(source)


def test_validate_rejects_hook_and_requirements(tmp_path: Path) -> None:
    source = build_extension_package(
        tmp_path / "candidate", references=("hooks/install.sh",)
    )
    with pytest.raises(ExtensionError, match="钩子|hook"):
        validate_extension(source)


# --- version path safety (评审问题一) ------------------------------------------


@pytest.mark.parametrize(
    "bad_version",
    [".", "..", "a/b", "a\\b", "/tmp/absolute", "../../escape"],
)
def test_validate_rejects_unsafe_version_strings(tmp_path: Path, bad_version: str) -> None:
    source = build_extension_package(tmp_path / "candidate", version=bad_version)
    with pytest.raises(ExtensionError, match="版本|version"):
        validate_extension(source)


def test_install_rejects_absolute_version_without_touching_external_files(
    tmp_path: Path, monkeypatch
) -> None:
    """绝对路径版本不得使安装流程删除受管目录外的用户文件。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    outside = tmp_path / "outside-target"
    outside.mkdir()
    sentinel = outside / "user-file.txt"
    sentinel.write_text("用户数据\n", encoding="utf-8")
    source = build_extension_package(tmp_path / "candidate", version=str(outside))
    with pytest.raises(ExtensionError, match="版本|version"):
        install_extension(source)
    assert sentinel.read_text(encoding="utf-8") == "用户数据\n"
    assert (tmp_path / "data" / "hetu-stock" / "extensions" / "registry.json").exists() is False


def test_install_rejects_traversal_version_and_keeps_registry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    keep = escaped / "keep.txt"
    keep.write_text("keep\n", encoding="utf-8")
    source = build_extension_package(tmp_path / "candidate", version="../../escaped")
    with pytest.raises(ExtensionError, match="版本|version"):
        install_extension(source)
    assert keep.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "data" / "hetu-stock" / "extensions" / "registry.json").exists()


def test_install_rejects_symlinked_extension_dir_and_keeps_external_files(
    tmp_path: Path, monkeypatch
) -> None:
    """extensions/<ID> 是指向外部目录的链接时，安装合法版本也必须拒绝。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "data" / "hetu-stock" / "extensions"
    root.mkdir(parents=True)
    external = tmp_path / "external"
    (external / "1.0").mkdir(parents=True)
    notes = external / "1.0" / "notes.txt"
    notes.write_text("外部笔记\n", encoding="utf-8")
    (root / "x.demo.rules").symlink_to(external, target_is_directory=True)

    source = build_extension_package(tmp_path / "candidate", version="1.0")
    with pytest.raises(ExtensionError, match="受管"):
        install_extension(source)

    assert notes.read_text(encoding="utf-8") == "外部笔记\n"
    assert (external / "1.0").is_dir()
    assert not (tmp_path / "data" / "hetu-stock" / "extensions" / "registry.json").exists()


def test_update_rejects_symlinked_extension_dir_and_keeps_installed_state(
    tmp_path: Path, monkeypatch
) -> None:
    """update 与 install 共用链接边界：拒绝时已安装包、外部文件和登记表不变。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    registry_before = _registry(tmp_path / "data" / "hetu-stock")
    managed = tmp_path / "data" / "hetu-stock" / "extensions" / "x.demo.rules"
    installed_before = (managed / "0.1.0" / "extension.json").read_bytes()

    backup = tmp_path / "backup"
    managed.rename(backup)
    external = tmp_path / "external"
    (external / "0.2.0").mkdir(parents=True)
    notes = external / "0.2.0" / "notes.txt"
    notes.write_text("外部笔记\n", encoding="utf-8")
    managed.symlink_to(external, target_is_directory=True)

    newer = build_extension_package(tmp_path / "candidate-v2", version="0.2.0")
    with pytest.raises(ExtensionError, match="受管"):
        update_extension(newer)

    assert notes.read_text(encoding="utf-8") == "外部笔记\n"
    assert (external / "0.2.0").is_dir()
    assert _registry(tmp_path / "data" / "hetu-stock") == registry_before
    assert (backup / "0.1.0" / "extension.json").read_bytes() == installed_before


def test_update_rejects_unsafe_version_and_keeps_installed_state(
    tmp_path: Path, monkeypatch
) -> None:
    """install/update 共用边界：拒绝时外部文件、已安装包和登记表都不变。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    registry_before = _registry(tmp_path / "data" / "hetu-stock")
    installed_before = (
        tmp_path / "data" / "hetu-stock" / "extensions" / "x.demo.rules" / "0.1.0"
    )
    before_file = (installed_before / "extension.json").read_bytes()

    hostile = build_extension_package(tmp_path / "candidate-v2", version="../0.1.0")
    with pytest.raises(ExtensionError, match="版本|version"):
        update_extension(hostile)

    registry_after = _registry(tmp_path / "data" / "hetu-stock")
    assert registry_after == registry_before
    assert set(registry_after["extensions"]["x.demo.rules"]["versions"]) == {"0.1.0"}
    assert registry_after["bindings"]["codex"]["x.demo.rules"]["version"] == "0.1.0"
    assert (installed_before / "extension.json").read_bytes() == before_file


# --- dependency and identity validation (评审问题二) ----------------------------


def test_context_propagates_integrity_failure_to_dependents(
    tmp_path: Path, monkeypatch
) -> None:
    """A 完整性失败后，依赖 A 的 B 不能因预先收集的 provides 进入候选。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    base = build_extension_package(tmp_path / "base")
    install_extension(base)
    dependent = build_extension_package(
        tmp_path / "dependent",
        extension_id="x.demo.dependent",
        package_ids=(("x.demo.dependent", ("x.demo.rules",), (), ()),),
    )
    install_extension(dependent)
    unrelated = build_extension_package(
        tmp_path / "unrelated",
        extension_id="x.demo.unrelated",
        package_ids=(("x.demo.unrelated", (), (), ()),),
    )
    install_extension(unrelated)
    enable_extension("codex", "x.demo.rules")
    enable_extension("codex", "x.demo.dependent")
    enable_extension("codex", "x.demo.unrelated")

    installed_base = (
        tmp_path
        / "data"
        / "hetu-stock"
        / "extensions"
        / "x.demo.rules"
        / "0.1.0"
        / "work-packages"
        / "x.demo.rules.md"
    )
    installed_base.write_text("被篡改的正文\n", encoding="utf-8")

    context = context_for_host("codex")
    assert [c["id"] for c in context["candidates"]] == ["x.demo.unrelated"]
    assert "x.demo.rules" in context["reason"]
    assert "完整性" in context["reason"]
    assert "x.demo.dependent" in context["reason"]
    assert "硬依赖" in context["reason"]


def test_enable_satisfies_internal_work_package_dependency(
    tmp_path: Path, monkeypatch
) -> None:
    """同一扩展内的合法硬依赖由本包满足，enable 不得误判为未启用。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(
        tmp_path / "candidate",
        package_ids=(
            ("x.demo.a", ("x.demo.b",), (), ()),
            ("x.demo.b", (), (), ()),
        ),
    )
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    context = context_for_host("codex")
    assert [c["id"] for c in context["candidates"]] == ["x.demo.rules"]


def test_enable_and_context_reject_duplicate_work_package_ids(
    tmp_path: Path, monkeypatch
) -> None:
    """不同扩展提供相同工作包 ID 时不能同时进入同一加载集合。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    first = build_extension_package(
        tmp_path / "first",
        extension_id="x.demo.one",
        package_ids=(("x.demo.shared", (), (), ()),),
    )
    second = build_extension_package(
        tmp_path / "second",
        extension_id="x.demo.two",
        package_ids=(("x.demo.shared", (), (), ()),),
    )
    install_extension(first)
    install_extension(second)
    enable_extension("codex", "x.demo.one")
    with pytest.raises(ExtensionError, match="冲突"):
        enable_extension("codex", "x.demo.two")
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert "x.demo.two" not in registry["bindings"]["codex"]
    assert registry["bindings"]["codex"]["x.demo.one"]["version"] == "0.1.0"

    # 既有绑定若已造成冲突（如登记被外部改动），context 两个都拒绝且不靠顺序选择。
    registry["bindings"]["codex"]["x.demo.two"] = {
        "version": "0.1.0",
        "granted_capabilities": ["local-files-read"],
        "enabled_at": "2026-09-12T00:00:00Z",
    }
    (tmp_path / "data" / "hetu-stock" / "extensions" / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    context = context_for_host("codex")
    assert context["candidates"] == []
    assert "x.demo.one" in context["reason"]
    assert "x.demo.two" in context["reason"]
    assert "冲突" in context["reason"]


# --- registry lifecycle -------------------------------------------------------


def test_install_saves_candidate_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    registry = _registry(tmp_path / "data" / "hetu-stock")
    entry = registry["extensions"]["x.demo.rules"]["versions"]["0.1.0"]
    assert entry["path"]
    assert registry["bindings"].get("codex", {}).get("x.demo.rules") is None
    assert entry["files"]["extension.json"]


def test_enable_disable_is_per_host(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert registry["bindings"]["codex"]["x.demo.rules"]["version"] == "0.1.0"
    for host in ("claude", "opencode", "zcode"):
        assert host not in registry["bindings"] or "x.demo.rules" not in registry["bindings"][host]
    disable_extension("codex", "x.demo.rules")
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert "x.demo.rules" not in registry["bindings"]["codex"]


def test_enable_multiple_hosts_then_disable_one_keeps_others(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    enable_extension("claude", "x.demo.rules")
    disable_extension("codex", "x.demo.rules")
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert "x.demo.rules" not in registry["bindings"]["codex"]
    assert registry["bindings"]["claude"]["x.demo.rules"]["version"] == "0.1.0"


def test_published_version_is_read_only_and_update_keeps_old(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    with pytest.raises(ExtensionError, match="版本|version"):
        install_extension(source)

    newer = build_extension_package(
        tmp_path / "candidate-v2", version="0.2.0", summary="第二版规则"
    )
    changes = update_extension(newer)
    assert changes["previous_version"] == "0.1.0"
    assert changes["version"] == "0.2.0"
    assert changes["summary_changed"] is True
    registry = _registry(tmp_path / "data" / "hetu-stock")
    versions = registry["extensions"]["x.demo.rules"]["versions"]
    assert set(versions) == {"0.1.0", "0.2.0"}
    # 更新不自动迁移绑定：旧绑定保持旧版本，直到用户重新启用新版。
    assert (
        registry["bindings"]["codex"]["x.demo.rules"]["version"] == "0.1.0"
    )


def test_uninstall_fails_while_another_host_still_binds(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("codex", "x.demo.rules")
    enable_extension("claude", "x.demo.rules")
    with pytest.raises(ExtensionError, match="绑定|binding|宿主"):
        uninstall_extension("x.demo.rules")
    disable_extension("claude", "x.demo.rules")
    with pytest.raises(ExtensionError, match="绑定|binding|宿主"):
        uninstall_extension("x.demo.rules")
    disable_extension("codex", "x.demo.rules")
    uninstall_extension("x.demo.rules")
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert "x.demo.rules" not in registry["extensions"]


@pytest.mark.parametrize("escape", ["absolute", "relative"])
def test_uninstall_refuses_registry_key_escaping_managed_root(
    tmp_path: Path, escape: str
) -> None:
    """评审阻断项：被篡改的登记表键不得让卸载删除受管根外的目录。

    拒绝时外部哨兵、登记表与其他宿主绑定均保持字节不变。
    """
    root = tmp_path / "data" / "hetu-stock" / "extensions"
    root.mkdir(parents=True)
    if escape == "absolute":
        sentinel_dir = tmp_path / "outside"
        evil_id = str(sentinel_dir)
    else:
        sentinel_dir = tmp_path / "data" / "hetu-stock" / "sentinel-rel"
        evil_id = "../sentinel-rel"
    sentinel_dir.mkdir()
    sentinel = sentinel_dir / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    _write_fake_registry(
        tmp_path / "data",
        {
            "extensions": {
                evil_id: {"versions": {"0.1.0": {"path": str(sentinel_dir)}}}
            },
            "bindings": {
                "codex": {},
                "claude": {"x.demo.other": {"version": "0.1.0"}},
                "opencode": {},
                "zcode": {},
            },
        },
    )
    registry_path = root / "registry.json"
    before = registry_path.read_bytes()
    with pytest.raises(ExtensionError, match="ID|受管|边界"):
        uninstall_extension(evil_id)
    assert sentinel.read_bytes() == b"do not delete"
    assert registry_path.read_bytes() == before


def test_enable_unknown_or_uninstalled_id_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with pytest.raises(ExtensionError, match="未安装|not installed|登记"):
        enable_extension("codex", "x.demo.rules")


# --- context (research loading surface, design §4.2) --------------------------


def test_context_returns_only_enabled_valid_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)

    empty = context_for_host("codex")
    assert empty["candidates"] == []
    assert empty["reason"]

    enable_extension("codex", "x.demo.rules")
    context = context_for_host("codex")
    assert [c["id"] for c in context["candidates"]] == ["x.demo.rules"]
    candidate = context["candidates"][0]
    assert candidate["version"] == "0.1.0"
    assert candidate["summary"]
    assert candidate["location"]
    # 只回元数据：正文字段不存在。
    assert "body" not in candidate
    # 其他宿主不加载。
    assert context_for_host("claude")["candidates"] == []


def test_context_closes_candidate_when_dependency_uninstalled(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    base = build_extension_package(tmp_path / "base")
    install_extension(base)
    dependent = build_extension_package(
        tmp_path / "dependent",
        extension_id="x.demo.dependent",
        package_ids=(("x.demo.dependent", ("x.demo.rules",), (), ()),),
    )
    install_extension(dependent)
    enable_extension("codex", "x.demo.rules")
    enable_extension("codex", "x.demo.dependent")
    assert [c["id"] for c in context_for_host("codex")["candidates"]] == [
        "x.demo.dependent",
        "x.demo.rules",
    ]
    disable_extension("codex", "x.demo.rules")
    context = context_for_host("codex")
    assert [c["id"] for c in context["candidates"]] == []
    assert "x.demo.dependent" in context["reason"]


def test_enable_and_context_exclude_candidate_with_unsupported_compat(
    tmp_path: Path, monkeypatch
) -> None:
    """enable/context 使用一致判定：版本不兼容在启用时即被拒绝。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate", compat="hetu-skill-v99")
    install_extension(source)
    with pytest.raises(ExtensionError, match="兼容"):
        enable_extension("codex", "x.demo.rules")
    context = context_for_host("codex")
    assert context["candidates"] == []


def test_list_and_inspect_report_registry_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = build_extension_package(tmp_path / "candidate")
    install_extension(source)
    enable_extension("zcode", "x.demo.rules")
    listed = list_extensions()
    assert listed["extensions"]["x.demo.rules"]["versions"] == ["0.1.0"]
    assert listed["bindings"]["zcode"]["x.demo.rules"]["version"] == "0.1.0"
    info = inspect_extension("x.demo.rules")
    assert info["id"] == "x.demo.rules"
    assert info["version"] == "0.1.0"
    assert "演示扩展" in info["summary"]
    assert "body" not in info


# --- default version ordering (5-A1) ------------------------------------------


def test_default_enable_inspect_and_update_use_numeric_aware_version_order(
    tmp_path: Path, monkeypatch
) -> None:
    """0.9.0 → 0.10.0 后 enable/inspect 默认选 0.10.0；update 同口径报告前一版。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install_extension(build_extension_package(tmp_path / "v0-9", version="0.9.0"))
    update_extension(build_extension_package(tmp_path / "v0-10", version="0.10.0"))

    enabled = enable_extension("codex", "x.demo.rules")
    assert enabled["version"] == "0.10.0"
    assert inspect_extension("x.demo.rules")["version"] == "0.10.0"

    changes = update_extension(
        build_extension_package(tmp_path / "v0-11", version="0.11.0")
    )
    assert changes["previous_version"] == "0.10.0"
    assert changes["version"] == "0.11.0"

    # update 不迁移既有绑定；显式旧版本仍可选；未登记版本受控拒绝。
    registry = _registry(tmp_path / "data" / "hetu-stock")
    assert registry["bindings"]["codex"]["x.demo.rules"]["version"] == "0.10.0"
    rebound = enable_extension("codex", "x.demo.rules", version="0.9.0")
    assert rebound["version"] == "0.9.0"
    with pytest.raises(ExtensionError, match="未登记"):
        enable_extension("codex", "x.demo.rules", version="0.10.1")


def test_default_version_order_handles_prefixed_and_mixed_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    """release-9/release-10 按数字段比较；数字与文字混合的登记表不崩溃。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install_extension(build_extension_package(tmp_path / "r9", version="release-9"))
    update_extension(build_extension_package(tmp_path / "r10", version="release-10"))
    assert enable_extension("claude", "x.demo.rules")["version"] == "release-10"
    assert inspect_extension("x.demo.rules")["version"] == "release-10"

    install_extension(
        build_extension_package(
            tmp_path / "mixed",
            extension_id="x.demo.mixed",
            version="2.0",
            package_ids=(("x.demo.mixed", (), (), ()),),
        )
    )
    update_extension(
        build_extension_package(
            tmp_path / "mixed-10",
            extension_id="x.demo.mixed",
            version="10.0",
            package_ids=(("x.demo.mixed", (), (), ()),),
        )
    )
    assert inspect_extension("x.demo.mixed")["version"] == "10.0"


def test_default_version_selection_breaks_numeric_ties_by_original_string(
    tmp_path: Path, monkeypatch
) -> None:
    """数字段相等（01.0 与 1.0）时以原字符串作最终平局键，结果确定。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install_extension(build_extension_package(tmp_path / "v01", version="01.0"))
    update_extension(build_extension_package(tmp_path / "v1", version="1.0"))
    assert enable_extension("codex", "x.demo.rules")["version"] == "1.0"


def test_default_version_prefers_extended_dotted_suffix(
    tmp_path: Path, monkeypatch
) -> None:
    """1.0 → 1.0.1 后默认选 1.0.1：分段序列按前缀语义比较，原字符串平局键
    不得提前参与分段比较（5-A1 复评缺口）。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install_extension(build_extension_package(tmp_path / "v1-0", version="1.0"))
    update_extension(build_extension_package(tmp_path / "v1-0-1", version="1.0.1"))

    assert enable_extension("codex", "x.demo.rules")["version"] == "1.0.1"
    assert inspect_extension("x.demo.rules")["version"] == "1.0.1"

    changes = update_extension(
        build_extension_package(tmp_path / "v1-0-2", version="1.0.2")
    )
    assert changes["previous_version"] == "1.0.1"
    assert changes["version"] == "1.0.2"
