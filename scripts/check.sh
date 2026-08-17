#!/usr/bin/env bash
set -euo pipefail

hetu_python="${HETU_PYTHON:-.venv/bin/python}"
hetu_cli="${HETU_CLI:-.venv/bin/hetu-stock}"
test -x "$hetu_python"
test -x "$hetu_cli"

"$hetu_python" -m pytest --collect-only -q
"$hetu_python" -m pytest -q tests/product tests/helpers
"$hetu_python" -m ruff check src tests scripts
"$hetu_python" -m mypy src
"$hetu_python" scripts/check_docs.py
"$hetu_python" scripts/update_skill_manifest.py
git diff --exit-code -- skills/hetu-stock-analysis/MANIFEST.json
"$hetu_cli" skill validate skills/hetu-stock-analysis
git diff --check
