import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hetu_stock.cli import app

runner = CliRunner()


def _copy_canonical_skill(tmp_path: Path) -> Path:
    target = tmp_path / "hetu-stock-analysis"
    shutil.copytree(Path("skills/hetu-stock-analysis"), target)
    return target


def test_skill_validate_accepts_canonical_package() -> None:
    result = runner.invoke(app, ["skill", "validate", "skills/hetu-stock-analysis"])

    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()


def test_skill_validate_missing_package_exits_cleanly() -> None:
    result = runner.invoke(app, ["skill", "validate", str(Path("/no/skill"))])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.strip()


def test_skill_validate_invalid_utf8_package_exits_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"\xff\xfe")

    result = runner.invoke(app, ["skill", "validate", str(root)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.strip()


def test_skill_validate_rejects_tampered_package(tmp_path: Path) -> None:
    target = _copy_canonical_skill(tmp_path)
    victim = target / "references" / "orchestration.md"
    victim.write_bytes(victim.read_bytes() + b"tampered")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "sha256 mismatch" in result.output


def test_skill_validate_rejects_package_with_extra_file(tmp_path: Path) -> None:
    target = _copy_canonical_skill(tmp_path)
    (target / "rogue.md").write_text("not in manifest", encoding="utf-8")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_skill_validate_requires_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: hetu-stock-analysis\ndescription: dev tree\n---\n\nbody\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["skill", "validate", str(root)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "MANIFEST.json" in result.output


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("{not json\n", "MANIFEST.json is invalid"),
        ("[]\n", "MANIFEST.json must contain a 'files' object"),
        ("{}\n", "MANIFEST.json must contain a 'files' object"),
        ('{"files": []}\n', "MANIFEST.json 'files' must be a mapping"),
    ],
)
def test_skill_validate_rejects_malformed_manifest_without_traceback(
    tmp_path: Path, manifest: str, message: str
) -> None:
    target = _copy_canonical_skill(tmp_path)
    target.joinpath("MANIFEST.json").write_text(manifest, encoding="utf-8")

    result = runner.invoke(app, ["skill", "validate", str(target)])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "invalid skill package:" in result.output
    assert message in result.output


def test_public_skill_validate_command_hides_malformed_manifest_traceback(
    tmp_path: Path,
) -> None:
    target = _copy_canonical_skill(tmp_path)
    target.joinpath("MANIFEST.json").write_text("{not json\n", encoding="utf-8")
    executable = Path(sys.executable).with_name("hetu-stock")

    result = subprocess.run(
        [str(executable), "skill", "validate", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Traceback" not in output
    assert "invalid skill package: MANIFEST.json is invalid" in output
