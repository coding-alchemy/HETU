#!/usr/bin/env bash
set -euo pipefail

hetu_python="${HETU_PYTHON:-.venv/bin/python}"
hetu_cli="${HETU_CLI:-.venv/bin/hetu-stock}"
test -x "$hetu_python"
test -x "$hetu_cli"

"$hetu_python" -m pytest --collect-only -q
"$hetu_python" -m pytest -q tests/product tests/helpers
# 显式选定证据的离线复核：仅在设置 HETU_HOST_EVIDENCE 时追加同一 check 入口；
# 未设置时保持完全离线的工程门禁，不启动任何宿主或模型。
if [[ -n "${HETU_HOST_EVIDENCE:-}" ]]; then
  echo "HETU_HOST_EVIDENCE=${HETU_HOST_EVIDENCE}: 追加显式选定证据的离线 check"
  "$hetu_python" scripts/host_acceptance.py check --host-evidence "$HETU_HOST_EVIDENCE"
fi
"$hetu_python" -m ruff check src tests scripts skills/hetu-stock-analysis/scripts
"$hetu_python" -m mypy src
"$hetu_python" scripts/check_docs.py
"$hetu_python" scripts/update_skill_manifest.py
git diff --exit-code -- skills/hetu-stock-analysis/MANIFEST.json
"$hetu_cli" skill validate skills/hetu-stock-analysis
git diff --check
