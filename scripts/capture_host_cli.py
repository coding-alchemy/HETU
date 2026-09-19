"""Capture read-only host CLI protocol snapshots into controlled fixtures (07.1b).

For each known host this runs only ``--version`` and ``--help`` (read-only;
never starts a session or sends a request) and refreshes the fixture under
``tests/product/fixtures/host_cli/``:

- ``version`` / ``help_text`` / ``captured_at`` always reflect this capture.
- Hosts without an installed CLI are recorded as ``status: unavailable`` with
  no protocol claims — newer fields are never guessed.
- ``protocol.accepted_flags`` / ``protocol.checked_absent`` are curated
  whitelists: on first capture ``accepted_flags`` is initialised from the
  observed help flags; on refresh the previous curated lists are kept so that
  host version drift surfaces as a test failure instead of a silent rewrite.
  Re-curate them by hand after reviewing the new help text.

Usage: ``python scripts/capture_host_cli.py [--host claude ...]``
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

KNOWN_HOSTS = ("codex", "claude", "opencode", "zcode")
DEFAULT_FIXTURES_DIR = Path("tests/product/fixtures/host_cli")
_FLAG_LINE_RE = re.compile(r"^\s+(-{1,2}[a-zA-Z][a-zA-Z0-9-]*)")
_FLAG_TOKEN_RE = re.compile(r"(?<![\w-])(-{1,2}[a-zA-Z][a-zA-Z0-9-]*)")


def _observed_flags(help_text: str) -> list[str]:
    flags: set[str] = set()
    for line in help_text.splitlines():
        match = _FLAG_LINE_RE.match(line)
        if match:
            flags.update(_FLAG_TOKEN_RE.findall(match.group(0)))
    return sorted(flags)


def _load_existing_protocol(fixtures_dir: Path, host: str) -> dict[str, list[str]]:
    path = fixtures_dir / f"{host}.json"
    if not path.is_file():
        return {"accepted_flags": [], "checked_absent": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"accepted_flags": [], "checked_absent": []}
    protocol = payload.get("protocol") or {}
    return {
        "accepted_flags": [
            str(flag) for flag in protocol.get("accepted_flags", [])
        ],
        "checked_absent": [
            str(flag) for flag in protocol.get("checked_absent", [])
        ],
    }


def capture_host(host: str, fixtures_dir: Path) -> dict[str, object]:
    binary = shutil.which(host)
    captured_at = datetime.now(UTC).date().isoformat()
    existing = _load_existing_protocol(fixtures_dir, host)
    if binary is None:
        return {
            "host": host,
            "status": "unavailable",
            "captured_at": captured_at,
            "version": None,
            "help_text": None,
            "protocol": {"accepted_flags": [], "checked_absent": []},
            "notes": "宿主 CLI 未安装，未捕获任何协议字段，不猜测新版本。",
        }

    version = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    help_result = subprocess.run(
        [binary, "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    help_text = (help_result.stdout or "") + (
        "\n" + help_result.stderr if help_result.stderr else ""
    )
    accepted = existing["accepted_flags"] or _observed_flags(help_text)
    return {
        "host": host,
        "status": "captured",
        "captured_at": captured_at,
        "version": version.stdout.strip() or version.stderr.strip() or None,
        "help_text": help_text,
        "protocol": {
            "accepted_flags": accepted,
            "checked_absent": existing["checked_absent"],
        },
        "notes": "只读捕获：仅执行 --version 与 --help，未发起会话或请求。",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        action="append",
        choices=KNOWN_HOSTS,
        help="host to capture (repeatable; default: all four)",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=DEFAULT_FIXTURES_DIR,
        help="fixture directory to refresh",
    )
    args = parser.parse_args(argv)
    hosts = args.host or list(KNOWN_HOSTS)
    args.fixtures_dir.mkdir(parents=True, exist_ok=True)
    for host in hosts:
        payload = capture_host(host, args.fixtures_dir)
        target = args.fixtures_dir / f"{host}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status = payload["status"]
        version = payload["version"] or "-"
        print(f"{host}: {status} ({version}) -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
