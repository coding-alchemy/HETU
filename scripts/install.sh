#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SKILL_SOURCE="$REPO_ROOT/skills/hetu-stock-analysis"
HOST=""
FORCE=0
PYTHON_REQUEST=""

usage() {
  printf '%s\n' "Usage: ./scripts/install.sh --host {codex|claude|opencode} [--python EXECUTABLE] [--force]"
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

select_python() {
  local candidate
  local resolved
  for candidate in python3.12 python3.11 python3; do
    if ! resolved="$(command -v "$candidate" 2>/dev/null)"; then
      continue
    fi
    if "$resolved" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

target_skill_path() {
  case "$HOST" in
    codex)
      printf '%s\n' "${CODEX_HOME:-$HOME/.codex}/skills/hetu-stock-analysis"
      ;;
    claude)
      printf '%s\n' "$HOME/.claude/skills/hetu-stock-analysis"
      ;;
    opencode)
      printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills/hetu-stock-analysis"
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || die "--host requires one of: codex, claude, opencode"
      HOST="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --python)
      [ "$#" -ge 2 ] || die "--python requires an executable name or path"
      PYTHON_REQUEST="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[ -n "$HOST" ] || die "--host is required; choose codex, claude, or opencode"
case "$HOST" in
  codex|claude|opencode) ;;
  *) die "Unsupported host '$HOST'; choose codex, claude, opencode" ;;
esac

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "V0.2 installer supports macOS and Linux only" ;;
esac

[ -n "${HOME:-}" ] || die "HOME must be set"
command -v git >/dev/null 2>&1 || die "Git is required"
[ -f "$SKILL_SOURCE/SKILL.md" ] || die "Canonical Skill not found at: $SKILL_SOURCE"
[ -f "$SKILL_SOURCE/MANIFEST.json" ] || die "Canonical Skill manifest not found at: $SKILL_SOURCE"

if [ -n "$PYTHON_REQUEST" ]; then
  if ! PYTHON="$(command -v "$PYTHON_REQUEST" 2>/dev/null)"; then
    die "Requested Python executable was not found: $PYTHON_REQUEST"
  fi
  "$PYTHON" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1 \
    || die "Requested interpreter must be Python 3.11 or 3.12: $PYTHON_REQUEST"
elif ! PYTHON="$(select_python)"; then
  die "Python 3.11 or 3.12 with venv support is required"
fi

DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
MANAGED_ROOT="$DATA_ROOT/hetu-stock"
VENV_ROOT="$MANAGED_ROOT/venv"
VENV_PYTHON="$VENV_ROOT/bin/python"
MANAGED_CLI="$VENV_ROOT/bin/hetu-stock"
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER="$LAUNCHER_DIR/hetu-stock"
TARGET_SKILL="$(target_skill_path)"

printf 'Using Python: %s\n' "$PYTHON"
printf 'Managed environment: %s\n' "$VENV_ROOT"

if [ -e "$VENV_ROOT" ]; then
  [ -x "$VENV_PYTHON" ] || die "Managed environment exists but has no executable Python: $VENV_ROOT"
  "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1 \
    || die "Managed environment must use Python 3.11 or 3.12: $VENV_ROOT"
else
  mkdir -p "$MANAGED_ROOT"
  if ! "$PYTHON" -m venv "$VENV_ROOT"; then
    die "Unable to create venv; install the venv component for Python 3.11 or 3.12"
  fi
fi

"$VENV_PYTHON" -m pip --version >/dev/null 2>&1 \
  || die "pip is unavailable in the managed environment: $VENV_ROOT"

printf '%s\n' "Installing optional deterministic helpers and Skill management CLI"
if ! "$VENV_PYTHON" -m pip install --upgrade "$REPO_ROOT"; then
  die "Python helper installation failed. Check network, proxy, and TLS certificate configuration; do not disable certificate verification"
fi
[ -x "$MANAGED_CLI" ] || die "Python helper installed without the hetu-stock command"

mkdir -p "$LAUNCHER_DIR"
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  if [ ! -L "$LAUNCHER" ] || [ "$(readlink "$LAUNCHER")" != "$MANAGED_CLI" ]; then
    die "Refusing to replace launcher not managed by HETU: $LAUNCHER"
  fi
  rm -- "$LAUNCHER"
fi
ln -s "$MANAGED_CLI" "$LAUNCHER"

printf '%s\n' "Validating the canonical Skill..."
"$LAUNCHER" skill validate "$SKILL_SOURCE"

if [ -e "$TARGET_SKILL" ] && [ "$FORCE" -eq 0 ]; then
  die "Skill already exists at $TARGET_SKILL; rerun with --force to replace it"
fi

install_args=(skill install --host "$HOST" --source "$SKILL_SOURCE")
if [ "$FORCE" -eq 1 ]; then
  install_args+=(--force)
fi
"$LAUNCHER" "${install_args[@]}"
"$LAUNCHER" skill validate "$TARGET_SKILL"
"$LAUNCHER" --help >/dev/null

printf '\nInstallation complete.\n'
printf 'CLI: %s\n' "$LAUNCHER"
printf 'Skill: %s\n' "$TARGET_SKILL"

case ":${PATH:-}:" in
  *":$LAUNCHER_DIR:"*) ;;
  *)
    printf '%s\n' 'PATH note: $HOME/.local/bin is not currently on PATH.'
    printf '%s\n' 'You can use $HOME/.local/bin/hetu-stock directly or add that directory to PATH.'
    ;;
esac
