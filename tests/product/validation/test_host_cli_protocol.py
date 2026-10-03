"""阶段 07.1b：宿主 CLI 协议夹具的离线合同检查。

夹具由 ``scripts/capture_host_cli.py`` 只读捕获（仅 ``--version`` / ``--help``），
记录每个宿主实际接受的协议字段白名单与明确检查过不存在的字段。本测试在本地与 CI
走同一检查器路径：

- 夹具结构完整：捕获日期、版本、帮助文本；未安装宿主记为 ``unavailable``，
  不得编造协议字段；
- 白名单自洽：每个 accepted 字段必须真实出现在捕获的帮助文本中，每个
  checked_absent 字段必须确认不存在；宿主换版本导致漂移时测试明确失败并
  提示重新捕获，绝不静默失效；
- 已安装宿主的本机漂移检查：``--version`` 实时输出与夹具不一致时失败并
  提示运行 ``scripts/capture_host_cli.py`` 重新捕获（CI 无宿主时跳过本项）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "host_cli"
KNOWN_HOSTS = ("codex", "claude", "opencode", "zcode")
RE_CAPTURE_HINT = (
    "宿主 CLI 版本与夹具不一致：请运行 "
    "`python scripts/capture_host_cli.py --host {host}` 重新捕获并人工复核白名单"
)
_FLAG_SHAPE_RE = re.compile(r"^--?[a-z0-9][a-z0-9-]*$", re.IGNORECASE)
_CAPTURED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_fixture(host: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{host}.json"
    assert path.is_file(), f"缺少宿主 CLI 夹具: {path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _flag_token_re(flag: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])")


@pytest.mark.parametrize("host", KNOWN_HOSTS)
def test_fixture_structure_is_complete(host: str) -> None:
    payload = _load_fixture(host)

    assert payload["host"] == host
    assert payload["status"] in ("captured", "unavailable")
    assert _CAPTURED_AT_RE.match(payload["captured_at"]), (
        f"{host}: captured_at 必须是捕获日期 YYYY-MM-DD"
    )
    protocol = payload["protocol"]
    assert isinstance(protocol, dict)
    assert isinstance(protocol["accepted_flags"], list)
    assert isinstance(protocol["checked_absent"], list)

    if payload["status"] == "unavailable":
        # 未安装宿主：不猜测任何协议字段，也不保留旧版本的帮助文本。
        assert payload["version"] is None
        assert payload["help_text"] is None
        assert protocol["accepted_flags"] == []
        assert protocol["checked_absent"] == []
    else:
        assert payload["version"]
        assert payload["help_text"]
        assert protocol["accepted_flags"], f"{host}: 已捕获宿主必须记录协议白名单"


@pytest.mark.parametrize("host", KNOWN_HOSTS)
def test_protocol_whitelist_matches_captured_help(host: str) -> None:
    payload = _load_fixture(host)
    if payload["status"] != "captured":
        pytest.skip(f"{host} 未安装，无帮助文本可核对")

    help_text = payload["help_text"]
    protocol = payload["protocol"]
    assert set(protocol["accepted_flags"]).isdisjoint(protocol["checked_absent"]), (
        f"{host}: accepted 与 checked_absent 不得重叠"
    )
    for flag in protocol["accepted_flags"]:
        assert _FLAG_SHAPE_RE.match(flag), f"{host}: 非法协议字段形态 {flag!r}"
        assert _flag_token_re(flag).search(help_text), (
            f"{host}: 白名单字段 {flag} 不在捕获的帮助文本中——"
            "宿主版本可能漂移，请重新捕获并复核白名单"
        )
    for flag in protocol["checked_absent"]:
        assert _FLAG_SHAPE_RE.match(flag), f"{host}: 非法协议字段形态 {flag!r}"
        assert not _flag_token_re(flag).search(help_text), (
            f"{host}: 记录为不存在的字段 {flag} 实际已出现在帮助文本中——"
            "请重新捕获并复核白名单"
        )


@pytest.mark.parametrize("host", KNOWN_HOSTS)
def test_unavailable_host_has_no_fabricated_protocol_fields(host: str) -> None:
    payload = _load_fixture(host)
    if payload["status"] == "captured":
        pytest.skip(f"{host} 已捕获，不适用未安装约束")
    # 未安装宿主不得记录任何版本或字段（绝不猜新版本字段）。
    assert payload["version"] is None
    assert payload["help_text"] is None
    assert payload["protocol"]["accepted_flags"] == []
    assert payload["protocol"]["checked_absent"] == []


@pytest.mark.parametrize("host", KNOWN_HOSTS)
def test_installed_host_version_matches_fixture(host: str) -> None:
    binary = shutil.which(host)
    if binary is None:
        pytest.skip(f"{host} 未安装，跳过本机漂移检查（CI 同路径）")
    payload = _load_fixture(host)
    live = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    live_version = (live.stdout or "").strip() or (live.stderr or "").strip()
    assert payload["status"] == "captured", (
        f"{host} 已安装但夹具记为 unavailable：{RE_CAPTURE_HINT.format(host=host)}"
    )
    assert live_version == payload["version"], (
        f"{host} 实时版本 {live_version!r} != 夹具版本 {payload['version']!r}："
        + RE_CAPTURE_HINT.format(host=host)
    )
