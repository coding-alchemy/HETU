"""6-B1：``scripts/capture_host_cli.py`` 的 flag 提取与策展白名单合同。

- ``_observed_flags`` 解析选项声明中的短长别名（``-h, --help``、
  ``-m, --model <MODEL>``）；描述区、参数值与示例中的类 flag 文本不参与提取；
- 真实夹具只读核对：提取结果必须覆盖策展 ``accepted_flags``，且不得包含
  ``checked_absent`` 项（checked_absent 表示已确认不存在），本测试不重捕或
  改写夹具；
- 首次初始化使用完整提取结果；刷新保留已有策展子集。全部用例 mock
  subprocess/shutil，不调用真实宿主二进制。
"""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "capture_host_cli.py"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "host_cli"
KNOWN_HOSTS = ("codex", "claude", "opencode", "zcode")

_MODULE = runpy.run_path(str(SCRIPT_PATH))

SYNTHETIC_HELP = """Demo CLI

Usage: demo [OPTIONS] [TARGET]
       demo --example

Options:
  -h, --help  Show help and exit
  -m, --model <MODEL>  Choose the model, overrides --config-file
      --config <KEY=VALUE>
          Override a config value; see --example for details
  --define <KEY --VALUE>  Define a key/value pair
  -v  Verbose output

Examples:
  demo --model fast --example
  --example foo
  --demo-flag  runs the demo
"""

SYNTHETIC_OBSERVED = ["--config", "--define", "--help", "--model", "-h", "-m", "-v"]


def _observed_flags(help_text: str) -> list[str]:
    return _MODULE["_observed_flags"](help_text)


def test_observed_flags_collects_short_long_aliases_from_declarations() -> None:
    assert _observed_flags(SYNTHETIC_HELP) == SYNTHETIC_OBSERVED


def test_observed_flags_excludes_descriptions_values_and_examples() -> None:
    observed = _observed_flags(SYNTHETIC_HELP)
    # --config-file 只出现在描述区，--example 只出现在 Usage/描述/示例中。
    assert "--config-file" not in observed
    assert "--example" not in observed
    # --VALUE 是 <KEY --VALUE> 占位符内部的类 flag 文本；--demo-flag 只在
    # Examples: 小节出现。二者都不是选项声明（6-B1 复评缺口）。
    assert "--VALUE" not in observed
    assert "--demo-flag" not in observed


def test_observed_flags_excludes_description_continuation_lines() -> None:
    """评审阻断项：无 2+ 空格分隔、含普通文本的缩进续行不是选项声明。

    变长占位符 ``<FILE>...`` 仍是合法声明的一部分，不得误判为续行。
    """
    help_text = (
        "Options:\n"
        "  --mode <MODE>  Modes are:\n"
        "                 --not-a-real-option means disabled\n"
        "  -i, --image <FILE>...\n"
    )
    observed = _observed_flags(help_text)
    assert "--mode" in observed
    assert "--not-a-real-option" not in observed
    assert observed.count("-i") == 1
    assert "--image" in observed


@pytest.mark.parametrize("host", KNOWN_HOSTS)
def test_observed_flags_cover_curated_whitelists_in_real_fixtures(host: str) -> None:
    payload = json.loads((FIXTURES_DIR / f"{host}.json").read_text(encoding="utf-8"))
    if payload["status"] != "captured":
        pytest.skip(f"{host} 夹具为 unavailable，无帮助文本可核对")
    observed = set(_observed_flags(payload["help_text"]))
    accepted = set(payload["protocol"]["accepted_flags"])
    assert accepted <= observed, (
        f"{host}: 策展白名单中的 {sorted(accepted - observed)} "
        "未在帮助文本的选项声明中提取到"
    )
    overclaimed = observed & set(payload["protocol"]["checked_absent"])
    assert not overclaimed, (
        f"{host}: checked_absent 中的 {sorted(overclaimed)} 实际出现在选项声明里，"
        "夹具需人工重新策展"
    )


def _fake_completed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    if "--version" in command:
        return subprocess.CompletedProcess(command, 0, stdout="demo-cli 1.2.3\n", stderr="")
    return subprocess.CompletedProcess(command, 0, stdout=SYNTHETIC_HELP, stderr="")


@pytest.fixture()
def fake_host_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda host: f"/usr/local/bin/{host}")
    monkeypatch.setattr(subprocess, "run", _fake_completed)


def test_first_capture_initializes_accepted_flags_from_full_observed(
    tmp_path: Path, fake_host_binary: None
) -> None:
    payload = _MODULE["capture_host"]("codex", tmp_path)
    assert payload["status"] == "captured"
    assert payload["version"] == "demo-cli 1.2.3"
    assert payload["help_text"] == SYNTHETIC_HELP
    assert payload["protocol"]["accepted_flags"] == SYNTHETIC_OBSERVED
    assert payload["protocol"]["checked_absent"] == []


def test_refresh_keeps_curated_accepted_subset_and_checked_absent(
    tmp_path: Path, fake_host_binary: None
) -> None:
    existing = {
        "host": "codex",
        "status": "captured",
        "captured_at": "2026-09-01",
        "version": "demo-cli 1.0.0",
        "help_text": "old help",
        "protocol": {"accepted_flags": ["--model"], "checked_absent": ["--frobnicate"]},
        "notes": "人工策展子集",
    }
    (tmp_path / "codex.json").write_text(json.dumps(existing), encoding="utf-8")
    # checked_absent 不得收容帮助文本中实际声明的选项。
    assert "--frobnicate" not in SYNTHETIC_OBSERVED

    payload = _MODULE["capture_host"]("codex", tmp_path)
    assert payload["protocol"]["accepted_flags"] == ["--model"]
    assert payload["protocol"]["checked_absent"] == ["--frobnicate"]
    # 帮助文本与版本仍按本次捕获刷新，只有策展列表被保留。
    assert payload["help_text"] == SYNTHETIC_HELP
    assert payload["version"] == "demo-cli 1.2.3"


def test_unavailable_host_records_no_protocol_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda host: None)
    payload = _MODULE["capture_host"]("zcode", tmp_path)
    assert payload["status"] == "unavailable"
    assert payload["version"] is None
    assert payload["help_text"] is None
    assert payload["protocol"] == {"accepted_flags": [], "checked_absent": []}


def _write_captured_fixture(fixtures_dir: Path, host: str) -> Path:
    existing = {
        "host": host,
        "status": "captured",
        "captured_at": "2026-09-01",
        "version": "demo-cli 1.0.0",
        "help_text": "old help",
        "protocol": {"accepted_flags": ["--model"], "checked_absent": []},
        "notes": "已有有效捕获",
    }
    path = fixtures_dir / f"{host}.json"
    path.write_text(json.dumps(existing), encoding="utf-8")
    return path


def test_refresh_without_binary_preserves_existing_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """评审阻断项：本机缺少宿主程序不得覆盖已有有效捕获。

    已有文件逐字节不变，输出明示本次刷新未完成且已有捕获已保留，
    命令非零退出。
    """
    path = _write_captured_fixture(tmp_path, "codex")
    before = path.read_bytes()
    monkeypatch.setattr(shutil, "which", lambda host: None)
    exit_code = _MODULE["main"](["--host", "codex", "--fixtures-dir", str(tmp_path)])
    assert exit_code != 0
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "未更新" in out or "未完成" in out
    assert "保留" in out


def test_refresh_without_binary_records_unavailable_on_first_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次没有记录时仍记录 unavailable，不猜测能力。"""
    monkeypatch.setattr(shutil, "which", lambda host: None)
    exit_code = _MODULE["main"](["--host", "zcode", "--fixtures-dir", str(tmp_path)])
    assert exit_code == 0
    payload = json.loads((tmp_path / "zcode.json").read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert payload["protocol"] == {"accepted_flags": [], "checked_absent": []}


def test_refresh_without_binary_refreshes_existing_unavailable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既有记录为 unavailable（非有效捕获）时可正常重写。"""
    path = tmp_path / "zcode.json"
    path.write_text(
        json.dumps({"host": "zcode", "status": "unavailable"}), encoding="utf-8"
    )
    monkeypatch.setattr(shutil, "which", lambda host: None)
    exit_code = _MODULE["main"](["--host", "zcode", "--fixtures-dir", str(tmp_path)])
    assert exit_code == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"


def test_main_writes_fixture_files_without_real_binaries(
    tmp_path: Path, fake_host_binary: None
) -> None:
    exit_code = _MODULE["main"](["--host", "codex", "--fixtures-dir", str(tmp_path)])
    assert exit_code == 0
    payload = json.loads((tmp_path / "codex.json").read_text(encoding="utf-8"))
    assert payload["host"] == "codex"
    assert payload["protocol"]["accepted_flags"] == SYNTHETIC_OBSERVED
