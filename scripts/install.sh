#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SKILL_SOURCE="$REPO_ROOT/skills/hetu-stock-analysis"
HOST=""
FORCE=0
PYTHON_REQUEST=""

usage() {
  printf '%s\n' "Usage: ./scripts/install.sh --host {codex|claude|opencode|zcode} [--python EXECUTABLE] [--force]"
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
    zcode)
      printf '%s\n' "$HOME/.zcode/skills/hetu-stock-analysis"
      ;;
  esac
}

managed_root_of() {
  # <root>/envs/env-<token>/bin/hetu-stock -> <root>
  # <root>/venv/bin/hetu-stock (v0.2-era official layout) -> <root>
  local env_dir="${1%/bin/hetu-stock}"
  case "$env_dir" in
    */envs/env-*) printf '%s\n' "${env_dir%/*/*}" ;;
    */venv) printf '%s\n' "${env_dir%/*}" ;;
    *) return 1 ;;
  esac
}

repo_version() {
  sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" 2>/dev/null | head -n 1
}

env_dist_version() {
  # The version of the installed hetu_stock distribution in an environment.
  local metadata
  for metadata in "$1"/lib/python3.*/site-packages/hetu_stock-*.dist-info/METADATA; do
    [ -f "$metadata" ] || continue
    sed -n 's/^Version: //p' "$metadata" | head -n 1
    return 0
  done
  return 1
}

record_field() {
  # Extract a string field from the combo record; empty when absent/null.
  sed -n "s/.*\"$2\": \"\(.*\)\".*/\1/p" "$1" 2>/dev/null | head -n 1
}

host_of_target() {
  # Derive the host from an installed Skill target path (for recovery of an
  # interrupted switch whose invoking host we only know from the record).
  case "$1" in
    "$HOME"/.claude/*|*/.claude/*) printf 'claude\n' ;;
    "$HOME"/.zcode/*|*/.zcode/*) printf 'zcode\n' ;;
    *"/opencode/skills/"*) printf 'opencode\n' ;;
    *) printf 'codex\n' ;;
  esac
}

write_host_ref() {
  # Keep the environment reference for one installed host; pruning consults
  # these files so environments still needed by not-recently-updated hosts
  # are never removed.
  mkdir -p "$MANAGED_ROOT/hosts"
  printf '{\n  "env": "%s",\n  "version": "%s",\n  "skill_target": "%s"\n}\n' \
    "$2" "$3" "$TARGET_SKILL" > "$MANAGED_ROOT/hosts/$1.json"
}

is_managed_cli_target() {
  # Ownership needs evidence, not just directory-name shape: another project
  # can use the same envs/env-* layout. Accepted proof: the combo record in
  # the owning managed root names this environment, or the environment holds
  # an installed hetu_stock distribution.
  local target="$1"
  local env_dir root site
  env_dir="${target%/bin/hetu-stock}"
  root="$(managed_root_of "$target")" || return 1
  [ -f "$target" ] || return 1
  if [ -f "$root/installation.json" ] && grep -qF "\"$env_dir\"" "$root/installation.json"; then
    return 0
  fi
  for site in "$env_dir"/lib/python3.*/site-packages; do
    [ -d "$site" ] || continue
    if compgen -G "$site/hetu_stock-*.dist-info" >/dev/null || [ -d "$site/hetu_stock" ]; then
      return 0
    fi
  done
  return 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || die "--host requires one of: codex, claude, opencode, zcode"
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

[ -n "$HOST" ] || die "--host is required; choose codex, claude, opencode, or zcode"
case "$HOST" in
  codex|claude|opencode|zcode) ;;
  *) die "Unsupported host '$HOST'; choose codex, claude, opencode, zcode" ;;
esac

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "Installer supports macOS and Linux only" ;;
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
ENVS_ROOT="$MANAGED_ROOT/envs"
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER="$LAUNCHER_DIR/hetu-stock"
TARGET_SKILL="$(target_skill_path)"

# The atomic directory exchange and the staging/backup area require the Skill
# target and the managed environments to stay on one filesystem.
if [ -e "$TARGET_SKILL" ] || [ -L "$TARGET_SKILL" ]; then
  [ "$FORCE" -eq 1 ] || die "Skill already exists at $TARGET_SKILL; rerun with --force to replace it"
fi
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  if [ ! -L "$LAUNCHER" ]; then
    die "Refusing to replace launcher not managed by HETU: $LAUNCHER"
  fi
  if ! is_managed_cli_target "$(readlink "$LAUNCHER")"; then
    die "Refusing to replace launcher not managed by HETU: $LAUNCHER"
  fi
fi

# Recover an interrupted switch: the record is persisted (state "switching")
# before the launcher moves, covering the window between the Skill publish
# and a finalized record. Complete the switch when the pending environment is
# usable; otherwise roll back to the previous combination. Anything
# unrecoverable is reported with the scene preserved — never guessed.
RECORD_STATE="$(record_field "$MANAGED_ROOT/installation.json" state || true)"
if [ "$RECORD_STATE" = "switching" ]; then
  PENDING_ENV="$(record_field "$MANAGED_ROOT/installation.json" env || true)"
  PENDING_CLI="$PENDING_ENV/bin/hetu-stock"
  PREVIOUS_ENV_CANDIDATE="$(record_field "$MANAGED_ROOT/installation.json" previous_env || true)"
  PENDING_TARGET="$(record_field "$MANAGED_ROOT/installation.json" skill_target || true)"
  PENDING_HOST="$(host_of_target "$PENDING_TARGET")"
  recovered=0
  if [ -x "$PENDING_CLI" ] && "$PENDING_CLI" --help >/dev/null 2>&1; then
    if [ "$(readlink "$LAUNCHER" 2>/dev/null || true)" != "$PENDING_CLI" ]; then
      RECOVER_TMP="$LAUNCHER_DIR/.hetu-stock.recover.$$"
      rm -f -- "$RECOVER_TMP"
      if ln -s "$PENDING_CLI" "$RECOVER_TMP" 2>/dev/null; then
        mv -f -- "$RECOVER_TMP" "$LAUNCHER" 2>/dev/null || rm -f -- "$RECOVER_TMP"
      fi
    fi
    if [ "$(readlink "$LAUNCHER" 2>/dev/null || true)" = "$PENDING_CLI" ] \
      && "$LAUNCHER" skill validate "$PENDING_TARGET" >/dev/null 2>&1; then
      PREVIOUS_ENV_JSON="null"
      [ -n "$PREVIOUS_ENV_CANDIDATE" ] && PREVIOUS_ENV_JSON="\"$PREVIOUS_ENV_CANDIDATE\""
      printf '{\n  "state": "finalized",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": "%s",\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
        "$PENDING_ENV" "$PREVIOUS_ENV_JSON" "$(record_field "$MANAGED_ROOT/installation.json" version || true)" "$(record_field "$MANAGED_ROOT/installation.json" previous_version || true)" "$LAUNCHER" "$PENDING_TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$MANAGED_ROOT/installation.json"
      write_host_ref "$PENDING_HOST" "$PENDING_ENV" "$(record_field "$MANAGED_ROOT/installation.json" version || true)"
      recovered=1
      printf '%s\n' "Recovered an interrupted installation; the combination is now consistent."
      if [ -d "$TARGET_SKILL" ]; then
        exit 0
      fi
    fi
  fi
  if [ "$recovered" -eq 0 ]; then
    PREVIOUS_CLI="$PREVIOUS_ENV_CANDIDATE/bin/hetu-stock"
    if [ -n "$PREVIOUS_ENV_CANDIDATE" ] && [ -x "$PREVIOUS_CLI" ] \
      && "$PREVIOUS_CLI" --help >/dev/null 2>&1; then
      if [ "$(readlink "$LAUNCHER" 2>/dev/null || true)" = "$PENDING_CLI" ]; then
        RECOVER_TMP="$LAUNCHER_DIR/.hetu-stock.recover.$$"
        rm -f -- "$RECOVER_TMP"
        if ln -s "$PREVIOUS_CLI" "$RECOVER_TMP" 2>/dev/null; then
          mv -f -- "$RECOVER_TMP" "$LAUNCHER" 2>/dev/null || rm -f -- "$RECOVER_TMP"
        fi
      fi
      if "$PREVIOUS_CLI" skill rollback --host "$PENDING_HOST" --destination "$(dirname -- "$PENDING_TARGET")" >/dev/null 2>&1; then
        rm -rf -- "$PENDING_ENV"
        PRIOR_ENV_JSON="null"
        PRIOR_ENV_VALUE="$(record_field "$MANAGED_ROOT/installation.json" prior_env || true)"
        [ -n "$PRIOR_ENV_VALUE" ] && PRIOR_ENV_JSON="\"$PRIOR_ENV_VALUE\""
        PRIOR_VERSION_JSON="null"
        PRIOR_VERSION_VALUE="$(record_field "$MANAGED_ROOT/installation.json" prior_previous_version || true)"
        [ -n "$PRIOR_VERSION_VALUE" ] && PRIOR_VERSION_JSON="\"$PRIOR_VERSION_VALUE\""
        printf '{\n  "state": "finalized",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": %s,\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
          "$PREVIOUS_ENV_CANDIDATE" "$PRIOR_ENV_JSON" "$(record_field "$MANAGED_ROOT/installation.json" previous_version || true)" "$PRIOR_VERSION_JSON" "$LAUNCHER" "$PENDING_TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
          > "$MANAGED_ROOT/installation.json"
        printf '%s\n' "Rolled back an interrupted installation; the previous combination was restored."
      else
        die "Interrupted installation cannot be recovered automatically (Skill rollback failed); the scene is preserved; run 'hetu-stock skill diagnose' and inspect $MANAGED_ROOT"
      fi
    else
      die "Interrupted installation cannot be recovered automatically (pending environment unusable and previous environment missing); the scene is preserved; inspect $MANAGED_ROOT"
    fi
  fi
fi

# Additional hosts share the managed helper environment. Installing a new
# host (no --force, no existing Skill for it) reuses the current managed
# environment when it carries the same version: creating one environment per
# host would overwrite the single combo record on every install and orphan
# earlier hosts' retained environments, which pruning then deletes.
if [ "$FORCE" -eq 0 ] && [ ! -e "$TARGET_SKILL" ] && [ ! -L "$TARGET_SKILL" ] \
  && [ -L "$LAUNCHER" ]; then
  REPO_VERSION="$(repo_version || true)"
  REUSE_ENV="$(record_field "$MANAGED_ROOT/installation.json" env || true)"
  if [ -z "$REUSE_ENV" ]; then
    REUSE_ENV="$(readlink "$LAUNCHER")"
    REUSE_ENV="${REUSE_ENV%/bin/hetu-stock}"
  fi
  REUSE_VERSION="$(record_field "$MANAGED_ROOT/installation.json" version || true)"
  if [ -z "$REUSE_VERSION" ]; then
    REUSE_VERSION="$(env_dist_version "$REUSE_ENV" || true)"
  fi
  if [ -n "$REPO_VERSION" ] && [ -n "$REUSE_VERSION" ] \
    && [ "$REUSE_VERSION" = "$REPO_VERSION" ] && [ -d "$REUSE_ENV" ]; then
    REUSE_CLI="$REUSE_ENV/bin/hetu-stock"
    if [ -x "$REUSE_CLI" ] && "$REUSE_CLI" --help >/dev/null 2>&1; then
      printf 'Reusing managed environment: %s\n' "$REUSE_ENV"
      if ! "$REUSE_CLI" skill install --host "$HOST" --source "$SKILL_SOURCE"; then
        die "Skill installation failed; the existing installation is unchanged"
      fi
      # Refresh the record timestamp; the combo fields stay as they are.
      PREVIOUS_ENV="null"
      PREVIOUS_ENV_VALUE="$(record_field "$MANAGED_ROOT/installation.json" previous_env || true)"
      [ -n "$PREVIOUS_ENV_VALUE" ] && PREVIOUS_ENV="\"$PREVIOUS_ENV_VALUE\""
      PREVIOUS_VERSION="null"
      PREVIOUS_VERSION_VALUE="$(record_field "$MANAGED_ROOT/installation.json" previous_version || true)"
      [ -n "$PREVIOUS_VERSION_VALUE" ] && PREVIOUS_VERSION="\"$PREVIOUS_VERSION_VALUE\""
      printf '{\n  "state": "finalized",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": %s,\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
        "$REUSE_ENV" "$PREVIOUS_ENV" "$REUSE_VERSION" "$PREVIOUS_VERSION" "$LAUNCHER" "$TARGET_SKILL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$MANAGED_ROOT/installation.json"
      write_host_ref "$HOST" "$REUSE_ENV" "$REUSE_VERSION"
      printf '\nInstallation complete.\n'
      printf 'CLI: %s\n' "$LAUNCHER"
      printf 'Skill: %s\n' "$TARGET_SKILL"
      exit 0
    fi
  fi
fi

ENV_TOKEN="$(date +%Y%m%d%H%M%S)-$$"
NEW_ENV="$ENVS_ROOT/env-$ENV_TOKEN"
NEW_CLI="$NEW_ENV/bin/hetu-stock"
NEW_PYTHON="$NEW_ENV/bin/python"

cleanup_new_env() {
  rm -rf -- "$NEW_ENV"
}

printf 'Using Python: %s\n' "$PYTHON"
printf 'New managed environment: %s\n' "$NEW_ENV"

# Step 1: build the new versioned environment entirely in its final location;
# any failure here leaves the previous environment, launcher, and Skill alone.
mkdir -p "$ENVS_ROOT"
if ! "$PYTHON" -m venv "$NEW_ENV"; then
  cleanup_new_env
  die "Unable to create venv; install the venv component for Python 3.11 or 3.12"
fi
if ! "$NEW_PYTHON" -m pip --version >/dev/null 2>&1; then
  cleanup_new_env
  die "pip is unavailable in the new managed environment: $NEW_ENV"
fi
printf '%s\n' "Installing optional deterministic helpers and Skill management CLI"
if ! "$NEW_PYTHON" -m pip install --upgrade "$REPO_ROOT"; then
  cleanup_new_env
  die "Python helper installation failed. Check network, proxy, and TLS certificate configuration; do not disable certificate verification"
fi
if [ ! -x "$NEW_CLI" ]; then
  cleanup_new_env
  die "Python helper installed without the hetu-stock command"
fi
if ! "$NEW_CLI" --help >/dev/null 2>&1; then
  cleanup_new_env
  die "CLI self-check failed in the new managed environment; the previous installation is unchanged"
fi
printf '%s\n' "Validating the canonical Skill..."
if ! "$NEW_CLI" skill validate "$SKILL_SOURCE"; then
  cleanup_new_env
  die "Canonical Skill validation failed; the previous installation is unchanged"
fi

# Step 2: publish the Skill with the atomic installer (keeps its own backup).
install_args=(skill install --host "$HOST" --source "$SKILL_SOURCE")
if [ "$FORCE" -eq 1 ]; then
  install_args+=(--force)
fi
if ! "$NEW_CLI" "${install_args[@]}"; then
  cleanup_new_env
  die "Skill installation failed; the previous installation is unchanged"
fi

# Step 2.5: the Skill is published from here on. Persist enough to recover
# this combination before the launcher moves: a "switching" record naming the
# new environment, the previous combination, and the generation before that
# (prior_* fields) so a later failure or maintenance run can restore the old
# state exactly — never a "new Skill + old launcher" mixture.
CURRENT_ENV=""
if [ -L "$LAUNCHER" ]; then
  LAUNCHER_LINK_TARGET="$(readlink "$LAUNCHER")"
  CURRENT_ENV="${LAUNCHER_LINK_TARGET%/bin/hetu-stock}"
fi
OLD_RECORD_ENV="$(record_field "$MANAGED_ROOT/installation.json" env || true)"
OLD_RECORD_PREVIOUS="$(record_field "$MANAGED_ROOT/installation.json" previous_env || true)"
OLD_RECORD_VERSION="$(record_field "$MANAGED_ROOT/installation.json" version || true)"
OLD_RECORD_PREV_VERSION="$(record_field "$MANAGED_ROOT/installation.json" previous_version || true)"
PREVIOUS_ENV_JSON="null"
[ -n "$CURRENT_ENV" ] && PREVIOUS_ENV_JSON="\"$CURRENT_ENV\""
PREVIOUS_VERSION_JSON="null"
if [ -n "$CURRENT_ENV" ]; then
  PREVIOUS_VERSION_VALUE="$(env_dist_version "$CURRENT_ENV" || true)"
  [ -n "$PREVIOUS_VERSION_VALUE" ] && PREVIOUS_VERSION_JSON="\"$PREVIOUS_VERSION_VALUE\""
fi
PRIOR_ENV_JSON="null"
[ -n "$OLD_RECORD_ENV" ] && PRIOR_ENV_JSON="\"$OLD_RECORD_ENV\""
PRIOR_PREVIOUS_JSON="null"
[ -n "$OLD_RECORD_PREVIOUS" ] && PRIOR_PREVIOUS_JSON="\"$OLD_RECORD_PREVIOUS\""
PRIOR_VERSION_JSON="null"
[ -n "$OLD_RECORD_VERSION" ] && PRIOR_VERSION_JSON="\"$OLD_RECORD_VERSION\""
PRIOR_PREV_VERSION_JSON="null"
[ -n "$OLD_RECORD_PREV_VERSION" ] && PRIOR_PREV_VERSION_JSON="\"$OLD_RECORD_PREV_VERSION\""
mkdir -p "$MANAGED_ROOT"
printf '{\n  "state": "switching",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": %s,\n  "prior_env": %s,\n  "prior_previous_env": %s,\n  "prior_version": %s,\n  "prior_previous_version": %s,\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
  "$NEW_ENV" "$PREVIOUS_ENV_JSON" "$(repo_version || true)" "$PREVIOUS_VERSION_JSON" "$PRIOR_ENV_JSON" "$PRIOR_PREVIOUS_JSON" "$PRIOR_VERSION_JSON" "$PRIOR_PREV_VERSION_JSON" "$LAUNCHER" "$TARGET_SKILL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$MANAGED_ROOT/installation.json"

# Test hook: simulate an interruption (kill) in the window between the
# persisted switching record and the launcher move. Only active when the
# variable is set (product tests).
if [ -n "${FAKE_SWITCH_FAIL:-}" ]; then
  die "Simulated interruption after the switching record was persisted"
fi

# Step 3: switch the launcher with a same-directory rename (atomic); remember
# the previous target so a later failure can restore it. Any failure from
# here until the record finalizes rolls the Skill back too — the previous
# installation is only "unchanged" when the Skill, launcher, and environment
# say so together.
rollback_skill_or_report() {
  if "$NEW_CLI" skill rollback --host "$HOST" --destination "$(dirname -- "$TARGET_SKILL")" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}
abort_switch() {
  rm -f -- "${TMP_LAUNCHER:-}"
  if rollback_skill_or_report; then
    cleanup_new_env
    # The old current environment is the launcher's target when known, else
    # what the previous record named.
    RESTORE_ENV="${CURRENT_ENV:-$OLD_RECORD_ENV}"
    RESTORE_ENV_JSON="null"
    [ -n "$RESTORE_ENV" ] && RESTORE_ENV_JSON="\"$RESTORE_ENV\""
    printf '{\n  "state": "finalized",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": "%s",\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
      "$RESTORE_ENV" "$PRIOR_PREVIOUS_JSON" "${PREVIOUS_VERSION_VALUE:-}" "${OLD_RECORD_PREV_VERSION:-}" "$LAUNCHER" "$TARGET_SKILL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      > "$MANAGED_ROOT/installation.json"
    die "$1; restored the previous Skill, launcher, and environment"
  fi
  die "$1; rolling back the Skill failed; the scene is preserved; run 'hetu-stock skill diagnose' and inspect $MANAGED_ROOT"
}
mkdir -p "$LAUNCHER_DIR" 2>/dev/null || abort_switch "Unable to prepare the launcher directory"
OLD_LAUNCHER_TARGET=""
if [ -L "$LAUNCHER" ]; then
  OLD_LAUNCHER_TARGET="$(readlink "$LAUNCHER")"
fi
TMP_LAUNCHER="$LAUNCHER_DIR/.hetu-stock.launcher.$$"
rm -f -- "$TMP_LAUNCHER"
ln -s "$NEW_CLI" "$TMP_LAUNCHER" 2>/dev/null || abort_switch "Unable to stage the launcher symlink"
restore_launcher() {
  rm -f -- "$TMP_LAUNCHER"
  if [ -n "$OLD_LAUNCHER_TARGET" ]; then
    ln -s "$OLD_LAUNCHER_TARGET" "$TMP_LAUNCHER"
    mv -f -- "$TMP_LAUNCHER" "$LAUNCHER"
  else
    rm -f -- "$LAUNCHER"
  fi
}
if ! mv -f -- "$TMP_LAUNCHER" "$LAUNCHER"; then
  abort_switch "Unable to switch the launcher"
fi

# Step 4: verify the switched combination through the launcher itself. On
# failure, close the combination completely: roll the Skill back to the
# version that matches the restored environment and launcher. --destination
# keeps the CLI rollback from touching the helper combo itself.
if ! "$LAUNCHER" skill validate "$TARGET_SKILL" >/dev/null 2>&1; then
  restore_launcher
  abort_switch "Post-switch validation failed"
fi
if ! "$LAUNCHER" --help >/dev/null 2>&1; then
  restore_launcher
  abort_switch "Post-switch CLI check failed"
fi

# Step 5: finalize the combo record immediately after the switch validated,
# so an interruption from here on finds a record that already describes the
# new combination (the version fields make the backup<->environment mapping
# explicit for diagnose and recovery). CURRENT_ENV/PREVIOUS_* and the
# OLD_RECORD_* fields were captured before the switch.
printf '{\n  "state": "finalized",\n  "env": "%s",\n  "previous_env": %s,\n  "version": "%s",\n  "previous_version": %s,\n  "launcher": "%s",\n  "skill_target": "%s",\n  "updated_at": "%s"\n}\n' \
  "$NEW_ENV" "$PREVIOUS_ENV_JSON" "$(repo_version || true)" "$PREVIOUS_VERSION_JSON" "$LAUNCHER" "$TARGET_SKILL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$MANAGED_ROOT/installation.json"
write_host_ref "$HOST" "$NEW_ENV" "$(repo_version || true)"

# Test hook: simulate an interruption right after the record write, before
# pruning. Only active when the variable is set (product tests).
if [ -n "${FAKE_RECORD_FAIL:-}" ]; then
  die "Simulated interruption after record write"
fi

# Step 6: keep only the new environment and the previous one (rollback combo);
# never prune an environment the combo record or an installed host's
# reference file still names.
for env_dir in "$ENVS_ROOT"/env-*; do
  [ -e "$env_dir" ] || continue
  [ "$env_dir" = "$NEW_ENV" ] && continue
  [ -n "$CURRENT_ENV" ] && [ "$env_dir" = "$CURRENT_ENV" ] && continue
  [ -n "$OLD_RECORD_ENV" ] && [ "$env_dir" = "$OLD_RECORD_ENV" ] && continue
  [ -n "$OLD_RECORD_PREVIOUS" ] && [ "$env_dir" = "$OLD_RECORD_PREVIOUS" ] && continue
  if grep -qF "\"$env_dir\"" "$MANAGED_ROOT/installation.json"; then
    continue
  fi
  host_ref_kept=0
  for host_ref in "$MANAGED_ROOT"/hosts/*.json; do
    [ -e "$host_ref" ] || continue
    if grep -qF "\"$env_dir\"" "$host_ref"; then
      host_ref_kept=1
      break
    fi
  done
  [ "$host_ref_kept" -eq 1 ] && continue
  rm -rf -- "$env_dir"
done

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
