"""Phase-5 stage 06.3: read-only inspection and safe export of legacy runs.

Legacy schema-version-3 directories are plain JSON; unknown fields are kept
verbatim, source bytes never change, and nothing in the archive is executed.
The old RunState/StageResult modules are never imported here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hetu_stock.helpers.archive import inspect_archive

ARCHIVE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "product" / "fixtures" / "archive" / "legacy-run"
)


@pytest.fixture()
def legacy_run(tmp_path: Path) -> Path:
    target = tmp_path / "legacy-run"
    shutil.copytree(ARCHIVE_FIXTURE, target)
    return target


def test_unknown_fields_are_preserved_without_mutating_source(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    path = source / "state.json"
    payload = {"schema_version": "3", "unknown_field": {"value": "保留原值"}}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    result = inspect_archive(source)
    assert result["raw_records"]["state.json"] == payload
    assert path.read_bytes() == before


def test_known_fields_are_presented_per_field(legacy_run: Path) -> None:
    from hetu_stock.helpers.archive import inspect_archive

    result = inspect_archive(legacy_run)

    assert result["schema_version"] == "3"
    fields = result["fields"]
    assert fields["请求"]["subject"]["symbol"] == "000001.SZ"
    assert fields["论点"]["main_thesis"]
    assert fields["原采用"] == {"adopted_claim_ids": ["claim-s2-filing"]}
    assert fields["用户决定"][0]["action"] == "RETRY"
    attempts = fields["阶段尝试"]["2"]
    assert len(attempts) == 2  # 多次尝试逐次呈现
    assert attempts[0]["issues"][0]["message"] == "样本不足"
    assert result["raw_records"]["state.json"]["unknown_field"] == {"value": "保留原值"}
    assert "unknown_field" in result["records"]["state.json"]["unknown_fields"]
    assert "api_token" in result["records"]["state.json"]["restricted_keys"]


def test_stage_results_evidence_and_claims_are_presented(legacy_run: Path) -> None:
    from hetu_stock.helpers.archive import inspect_archive

    result = inspect_archive(legacy_run)

    record = result["records"]["stage_results/s5.json"]
    assert record["fields"]["证据"][0]["evidence_id"] == "ev-s5-note"
    assert record["fields"]["主张"][0]["claim_id"] == "claim-s5-note"
    assert "custom_vendor_field" in record["unknown_fields"]
    assert result["reports"] == ["report.md"]


def test_broken_json_missing_reference_and_unknown_version_are_scoped(
    legacy_run: Path,
) -> None:
    from hetu_stock.helpers.archive import inspect_archive

    result = inspect_archive(legacy_run)

    broken = [issue for issue in result["issues"] if issue["file"] == "broken.json"]
    assert broken and "JSON" in broken[0]["message"]
    missing = [
        issue
        for issue in result["issues"]
        if issue["file"] == "attachments/field-notes.md"
    ]
    assert missing

    other = legacy_run.parent / "other-version"
    other.mkdir()
    (other / "state.json").write_text(
        json.dumps({"schema_version": "9", "request": {}}), encoding="utf-8"
    )
    other_result = inspect_archive(other)
    assert any("版本" in issue["message"] for issue in other_result["issues"])
    assert other_result["raw_records"]["state.json"]["schema_version"] == "9"
    # 原件保留可取回
    assert (other / "state.json").exists()


def test_export_copies_allowed_records_and_indexes_redaction(
    legacy_run: Path, tmp_path: Path
) -> None:
    from hetu_stock.helpers.archive import export_archive

    output = tmp_path / "export"
    summary = export_archive(legacy_run, output)

    index = (output / "index.md").read_text(encoding="utf-8")
    assert "000001.SZ" in index
    assert "state.json" in index
    assert "脱敏" in index and "api_token" in index
    # 含秘密的原记录不复制到导出包，索引不含秘密值
    assert "fake-secret-value" not in index
    assert not (output / "records" / "state.json").exists()
    for exported_file in output.rglob("*"):
        if exported_file.is_file():
            assert "fake-secret-value" not in exported_file.read_text("utf-8")
    # 未受限记录原样复制，未知字段保留
    exported_stage = json.loads(
        (output / "records" / "stage_results" / "s5.json").read_text("utf-8")
    )
    assert exported_stage["custom_vendor_field"] == {"keep": "me"}
    assert (output / "records" / "report.md").exists()
    # 原件保留原位置，源字节不变
    assert "fake-secret-value-0000" in (legacy_run / "state.json").read_text("utf-8")
    assert any("api_token" in entry["locator"] for entry in summary["redacted"])
    assert {issue["file"] for issue in summary["issues"]} == {
        "broken.json",
        "attachments/field-notes.md",
    }


def test_export_rejects_existing_target_and_source_nesting(
    legacy_run: Path, tmp_path: Path
) -> None:
    from hetu_stock.helpers.archive import ArchiveError, export_archive

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ArchiveError, match="已存在|exist"):
        export_archive(legacy_run, existing)

    with pytest.raises(ArchiveError, match="源目录|source"):
        export_archive(legacy_run, legacy_run / "export")

    with pytest.raises(ArchiveError, match="已存在|exist"):
        export_archive(legacy_run, tmp_path / "existing")


def test_export_rejects_symlink_escape(legacy_run: Path, tmp_path: Path) -> None:
    from hetu_stock.helpers.archive import ArchiveError, export_archive

    outside = tmp_path / "outside.txt"
    outside.write_text("外部文件\n", encoding="utf-8")
    (legacy_run / "linked.md").symlink_to(outside)

    with pytest.raises(ArchiveError, match="符号链接|symlink|越界"):
        export_archive(legacy_run, tmp_path / "export")


def test_export_redacts_nested_json_attachment_with_secrets(
    legacy_run: Path, tmp_path: Path
) -> None:
    """attachments/ 下的 JSON 秘密不能被原样导出（评审问题三）。"""
    from hetu_stock.helpers.archive import export_archive

    attachments = legacy_run / "attachments"
    attachments.mkdir(exist_ok=True)
    credentials = attachments / "credentials.json"
    credentials.write_text(
        json.dumps({"service": "demo", "api_token": "fake-secret-token-1234"}),
        encoding="utf-8",
    )
    before = credentials.read_bytes()

    output = tmp_path / "export"
    summary = export_archive(legacy_run, output)

    assert not (output / "records" / "attachments" / "credentials.json").exists()
    assert any("credentials.json" in entry["locator"] for entry in summary["redacted"])
    index = (output / "index.md").read_text(encoding="utf-8")
    assert "fake-secret-token-1234" not in index
    for exported_file in output.rglob("*"):
        if exported_file.is_file():
            assert "fake-secret-token-1234" not in exported_file.read_text(
                "utf-8", errors="ignore"
            )
    # 源字节不变，原件保留原位置
    assert credentials.read_bytes() == before
    assert json.loads(credentials.read_text("utf-8"))["api_token"] == (
        "fake-secret-token-1234"
    )
    # 无秘密的正常附件仍按原样导出
    normal = attachments / "field-notes.md"
    normal.write_text("普通调研笔记\n", encoding="utf-8")
    output2 = tmp_path / "export2"
    summary2 = export_archive(legacy_run, output2)
    assert (output2 / "records" / "attachments" / "field-notes.md").exists()
    assert "field-notes.md" not in {
        entry["locator"].split("#")[0] for entry in summary2["redacted"]
    }


def test_export_redacts_json_array_attachment_with_secrets(
    legacy_run: Path, tmp_path: Path
) -> None:
    """顶层 JSON 数组中的秘密字段同样不能原样导出（残留二）。"""
    from hetu_stock.helpers.archive import export_archive

    attachments = legacy_run / "attachments"
    attachments.mkdir(exist_ok=True)
    credentials = attachments / "credentials.json"
    credentials.write_text(
        json.dumps([{"service": "demo", "api_token": "fake-secret-array-5678"}]),
        encoding="utf-8",
    )
    before = credentials.read_bytes()

    output = tmp_path / "export"
    summary = export_archive(legacy_run, output)

    assert not (output / "records" / "attachments" / "credentials.json").exists()
    assert any("credentials.json" in entry["locator"] for entry in summary["redacted"])
    index = (output / "index.md").read_text(encoding="utf-8")
    assert "fake-secret-array-5678" not in index
    for exported_file in output.rglob("*"):
        if exported_file.is_file():
            assert "fake-secret-array-5678" not in exported_file.read_text(
                "utf-8", errors="ignore"
            )
    assert credentials.read_bytes() == before


def test_export_keeps_json_array_attachment_without_secrets(
    legacy_run: Path, tmp_path: Path
) -> None:
    from hetu_stock.helpers.archive import export_archive

    attachments = legacy_run / "attachments"
    attachments.mkdir(exist_ok=True)
    plain = attachments / "list.json"
    plain.write_text(json.dumps([{"service": "demo"}]), encoding="utf-8")
    before = plain.read_bytes()

    output = tmp_path / "export"
    summary = export_archive(legacy_run, output)

    exported = (output / "records" / "attachments" / "list.json").read_bytes()
    assert exported == before
    assert "list.json" not in {
        entry["locator"].split("#")[0] for entry in summary["redacted"]
    }


def test_export_works_with_relative_source_and_output(
    legacy_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对 source/output 正常导出，布局与内容与绝对路径一致（残留三）。"""
    from hetu_stock.helpers.archive import export_archive

    monkeypatch.chdir(tmp_path)
    source = Path("old-run")
    shutil.copytree(legacy_run, source)
    before = (source / "state.json").read_bytes()

    summary = export_archive(Path("old-run"), Path("export"))

    output = Path("export")
    assert (output / "index.md").is_file()
    exported_stage = json.loads(
        (output / "records" / "stage_results" / "s5.json").read_text("utf-8")
    )
    assert exported_stage["custom_vendor_field"] == {"keep": "me"}
    assert exported_stage["claims"][0]["claim_id"] == "claim-s5-note"
    # 原相对布局与脱敏行为不变
    assert not (output / "records" / "state.json").exists()
    assert (output / "records" / "report.md").is_file()
    assert summary["output"] == str((tmp_path / "export").resolve())
    # 源字节不变
    assert (source / "state.json").read_bytes() == before


def test_export_resolves_symlinked_source_parent(
    legacy_run: Path, tmp_path: Path
) -> None:
    """经链接父目录（路径别名）访问源目录时，边界判断与相对定位一致。"""
    from hetu_stock.helpers.archive import export_archive

    real = tmp_path / "real"
    shutil.copytree(legacy_run, real)
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)

    export_archive(tmp_path / "alias", tmp_path / "export")

    assert (tmp_path / "export" / "index.md").is_file()
    assert not (tmp_path / "export" / "records" / "state.json").exists()
    # 索引按用户给定来源记录定位（既有行为），导出本身成功
    index = (tmp_path / "export" / "index.md").read_text(encoding="utf-8")
    assert str(tmp_path / "alias") in index


def test_export_alias_symlink_cannot_bypass_redaction(
    legacy_run: Path, tmp_path: Path
) -> None:
    """源内链接指向受限记录时，按解析后的实际对象判定，不得复制秘密。"""
    from hetu_stock.helpers.archive import export_archive

    alias = legacy_run / "alias.txt"
    alias.symlink_to(legacy_run / "state.json")

    output = tmp_path / "export"
    summary = export_archive(legacy_run, output)

    assert not (output / "records" / "alias.txt").exists()
    assert any("alias.txt" in entry["locator"] for entry in summary["redacted"])
    for exported_file in output.rglob("*"):
        if exported_file.is_file():
            assert "fake-secret-value-0000" not in exported_file.read_text(
                "utf-8", errors="ignore"
            )
    # 源链接保持不变
    assert alias.is_symlink()


def test_inspect_rejects_state_json_symlink_escape(tmp_path: Path) -> None:
    """state.json 指向源外文件时，外部内容不得进入 raw_records（评审问题四）。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / "private.json"
    private.write_text(
        json.dumps({"private_note": "external-secret-xyz"}), encoding="utf-8"
    )
    source = tmp_path / "old"
    source.mkdir()
    (source / "state.json").symlink_to(private)

    result = inspect_archive(source)

    assert "state.json" not in result["raw_records"]
    issue = [i for i in result["issues"] if i["file"] == "state.json"]
    assert issue and "越界" in issue[0]["message"]
    assert "external-secret-xyz" not in json.dumps(result, ensure_ascii=False)


def test_inspect_rejects_stage_results_directory_symlink_escape(tmp_path: Path) -> None:
    """stage_results 目录链接指向源外时同样被阻止。"""
    outside = tmp_path / "outside"
    (outside / "stage_results").mkdir(parents=True)
    (outside / "stage_results" / "s9.json").write_text(
        json.dumps({"stage_id": 9, "private": "external-secret-abc"}),
        encoding="utf-8",
    )
    source = tmp_path / "old"
    source.mkdir()
    (source / "state.json").write_text(
        json.dumps({"schema_version": "3", "request": {}}), encoding="utf-8"
    )
    (source / "stage_results").symlink_to(
        outside / "stage_results", target_is_directory=True
    )

    result = inspect_archive(source)

    assert "stage_results/s9.json" not in result["raw_records"]
    issue = [i for i in result["issues"] if "stage_results" in i["file"]]
    assert issue and any("越界" in i["message"] for i in issue)
    assert "external-secret-abc" not in json.dumps(result, ensure_ascii=False)
    # 源目录不被修改
    assert (source / "stage_results").is_symlink()


def test_import_boundaries_keep_legacy_modules_unimported() -> None:
    from hetu_stock.helpers import archive

    imported = {
        name
        for name in dir(archive)
        if name in {"RunState", "StageResult", "WorkflowState"}
    }
    assert imported == set()
