#!/usr/bin/env bash
# Atomic blue/green release deployment.  The checkout containing this script is
# a control repository only: serving processes never execute from it.
set -Eeuo pipefail
umask 027

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

resolve_environment_file() {
  local system_env="${1:-/etc/myportal.env}" selected
  if [[ -n "${MYPORTAL_ENV_FILE:-}" ]]; then
    selected="$MYPORTAL_ENV_FILE"
  elif [[ -f "$system_env" ]]; then
    # This is the EnvironmentFile used by deploy/systemd/myportal@.service.
    # Migrations must use the same credentials as the serving application.
    selected="$system_env"
  else
    # Backward compatibility for installations created by the legacy installer.
    selected="${PROJECT_ROOT}/.env"
  fi

  # Resolve symlink chains as well as relative paths. Otherwise invoking this
  # script through /opt/myportal/current can chain one release's .env to the
  # previous release instead of the persistent deployment configuration.
  python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$selected"
}

VENV_DIR="${PROJECT_ROOT}/.venv" # retained only to find the coordinator Python
ENV_FILE=$(resolve_environment_file)
RELEASE_ROOT="${MYPORTAL_RELEASE_ROOT:-/opt/myportal/releases}"
SHARED_ROOT="${MYPORTAL_SHARED_ROOT:-/opt/myportal/shared}"
INSTANCE_ROOT="${MYPORTAL_INSTANCE_ROOT:-/opt/myportal/instances}"
CURRENT_LINK="${MYPORTAL_CURRENT_LINK:-/opt/myportal/current}"
UPSTREAM_FILE="${MYPORTAL_NGINX_UPSTREAM_FILE:-/etc/nginx/conf.d/myportal-active.inc}"
READY_TIMEOUT="${MYPORTAL_READY_TIMEOUT:-60}"
DRAIN_SECONDS="${MYPORTAL_DRAIN_SECONDS:-15}"
SMOKE_PATH="${MYPORTAL_SMOKE_PATH:-/healthz}"
SYSTEM_UPDATE_STATUS_FILE="${SHARED_ROOT}/state/system_update.status"
REQUESTED_UPGRADE_MODE="rolling"
RESTART_MODE="rolling"
UPGRADE_READY_WAIT_SECONDS=0

usage() {
  cat <<'EOF'
Usage: upgrade.sh [--rolling] [--graceful | --restart] [--auto-fallback]

All update modes use the safe immutable blue/green release procedure. Legacy
mode flags remain accepted for callers, but never update a live checkout.
EOF
}

while (($#)); do
  case "$1" in
    --rolling|--graceful|--restart) REQUESTED_UPGRADE_MODE="${1#--}" ;;
    --auto-fallback) : ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

resolve_requested_upgrade_mode() { printf '%s' "${APP_UPGRADE_MODE:-$REQUESTED_UPGRADE_MODE}"; }
resolve_effective_upgrade_mode() { printf '%s' rolling; }

write_upgrade_status() {
  local status="$1" message="$2" reason="${3:-immutable_release}"
  mkdir -p "$(dirname "$SYSTEM_UPDATE_STATUS_FILE")"
  local tmp="${SYSTEM_UPDATE_STATUS_FILE}.$$"
  cat >"$tmp" <<EOF
started_at=${UPGRADE_STARTED_AT}
finished_at=$(date --iso-8601=seconds)
status=${status}
mode=${RESTART_MODE}
requested_mode=${REQUESTED_UPGRADE_MODE}
reason=${reason}
message=${message}
ready_wait_seconds=${UPGRADE_READY_WAIT_SECONDS}
EOF
  chmod 640 "$tmp" && mv -f "$tmp" "$SYSTEM_UPDATE_STATUS_FILE"
}

validate_origin_remote() {
  local url="$1"
  case "$url" in
    https://github.com/*|git@github.com:*|ssh://git@github.com/*) ;;
    *) echo "Refusing automatic update from untrusted origin: $url" >&2; return 1 ;;
  esac
  if [[ "$url" =~ ^https://[^/@]+:[^/@]+@ ]]; then
    echo "Refusing credential-bearing HTTPS remotes" >&2; return 1
  fi
}

validate_required_configuration() {
  local missing
  missing=$(ENV_CONFIG_FILE="$ENV_FILE" python3 - <<'PY'
import os
from pathlib import Path

required = ("SESSION_SECRET", "TOTP_ENCRYPTION_KEY")
values = dict(os.environ)
env_file = Path(os.environ["ENV_CONFIG_FILE"])

if env_file.is_file():
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in values:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name] = value

print(" ".join(name for name in required if not values.get(name)))
PY
  )
  if [[ -n "$missing" ]]; then
    echo "Missing required application configuration: ${missing}." >&2
    echo "Set the values in ${ENV_FILE} (or export them) before running the upgrade." >&2
    return 1
  fi
}

atomic_link() {
  local target="$1" link="$2" tmp
  tmp="${link}.new.$$"
  mkdir -p "$(dirname "$link")"
  ln -s "$target" "$tmp"
  mv -Tf "$tmp" "$link"
}

instance_port() { [[ "$1" == blue ]] && printf 8001 || printf 8002; }

write_upstream() {
  local active="$1" inactive="$2" tmp="${UPSTREAM_FILE}.new.$$"
  mkdir -p "$(dirname "$UPSTREAM_FILE")"
  printf 'server 127.0.0.1:%s max_fails=1 fail_timeout=5s;\nserver 127.0.0.1:%s down;\n' \
    "$(instance_port "$active")" "$(instance_port "$inactive")" >"$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$UPSTREAM_FILE"
  nginx -t
  nginx -s reload
}

read_active() {
  if [[ -f "$UPSTREAM_FILE" ]] && grep -q "127.0.0.1:8002 max_fails" "$UPSTREAM_FILE"; then
    printf green
  else
    printf blue
  fi
}

wait_for_version() {
  local port="$1" expected="$2" elapsed=0 body
  while ((elapsed < READY_TIMEOUT)); do
    body=$(curl -fsS --max-time 2 "http://127.0.0.1:${port}/readyz" 2>/dev/null || true)
    if [[ "$body" == *'"status":"ok"'* && "$body" == *"\"version\":\"${expected}\""* ]]; then return 0; fi
    sleep 1; ((elapsed+=1))
  done
  echo "Instance on port ${port} did not report expected version ${expected}" >&2
  return 1
}

smoke_test() {
  local port="$1" expected="$2" headers body
  headers=$(mktemp); body=$(mktemp)
  curl -fsS --max-time 10 -D "$headers" -o "$body" "http://127.0.0.1:${port}${SMOKE_PATH}"
  rm -f "$headers" "$body"
  wait_for_version "$port" "$expected"
}

install_dependencies() {
  local release="$1"
  python3 -m venv "${release}/.venv"
  "${release}/.venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
  "${release}/.venv/bin/python" -m pip install --disable-pip-version-check "$release"
}

prepare_release() {
  local revision="$1" release="$2" staging
  staging="${release}.staging.$$"
  if [[ -e "$release" ]]; then
    # A previous attempt may have prepared this revision with missing or stale
    # configuration. Refresh only the symlink; never copy or regenerate secrets.
    if [[ -f "$ENV_FILE" && "$(readlink "$release/.env" 2>/dev/null || true)" != "$ENV_FILE" ]]; then
      ln -sfn "$ENV_FILE" "$release/.env"
    fi
    return 0
  fi
  mkdir -p "$RELEASE_ROOT" "$SHARED_ROOT/state" "$SHARED_ROOT/data"
  rm -rf "$staging"; mkdir -p "$staging"
  git archive "$revision" | tar -x -C "$staging"
  printf '%s\n' "$revision" >"$staging/version.txt"
  # Settings resolves .env relative to the immutable release. Reuse the
  # installation's protected configuration rather than copying its secrets.
  if [[ -f "$ENV_FILE" ]]; then
    ln -s "$ENV_FILE" "$staging/.env"
  fi
  # Mutable state is shared, while code, templates, assets, and dependencies are
  # private to this revision.
  rm -rf "$staging/var"
  ln -s "$SHARED_ROOT" "$staging/var"
  install_dependencies "$staging"
  find "$staging" -type d -exec chmod a-w {} +
  find "$staging" -type f -exec chmod a-w {} +
  mv "$staging" "$release"
}

run_release_manage() {
  local release="$1"
  shift
  # Do not source .env in a shell: characters such as $, #, !, spaces, and
  # quotes must reach the application without expansion.  Disable dotenv
  # interpolation and make the protected file authoritative over any stale
  # DB_* values inherited by the upgrade process.
  ENV_CONFIG_FILE="$ENV_FILE" "${release}/.venv/bin/python" - \
    "${release}/manage.py" "$@" <<'PY'
import os
import sys

from dotenv import dotenv_values

env_file = os.environ["ENV_CONFIG_FILE"]
try:
    configured = dotenv_values(env_file, interpolate=False)
except (OSError, UnicodeError, ValueError) as exc:
    raise SystemExit(f"Unable to read application environment file {env_file}: {exc}") from None

for name, value in configured.items():
    if value is not None:
        os.environ[name] = value

os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
PY
}

rollback() {
  local old_active="$1" new_instance="$2" old_instance_release="$3"
  echo "Deployment failed; restoring ${old_active}." >&2
  if [[ -n "$old_instance_release" && -d "$old_instance_release" ]]; then
    atomic_link "$old_instance_release" "$INSTANCE_ROOT/$new_instance"
    systemctl restart "myportal@${new_instance}.service" >/dev/null 2>&1 || true
  fi
  write_upstream "$old_active" "$new_instance" || true
  [[ -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE" ]] && atomic_link "$PREVIOUS_RELEASE" "$CURRENT_LINK"
  write_upgrade_status failed "Cutover failed; previous release and upstream restored."
}

run_rolling_restart() {
  local revision="$1" release="$2" active inactive old_inactive start=$SECONDS
  active=$(read_active); [[ "$active" == blue ]] && inactive=green || inactive=blue
  old_inactive=$(readlink -f "$INSTANCE_ROOT/$inactive" 2>/dev/null || true)
  trap 'rollback "$active" "$inactive" "$old_inactive"' ERR

  # Only the non-serving slot changes during preparation and validation.
  atomic_link "$release" "$INSTANCE_ROOT/$inactive"
  systemctl restart "myportal@${inactive}.service"
  wait_for_version "$(instance_port "$inactive")" "$revision"
  smoke_test "$(instance_port "$inactive")" "$revision"

  # nginx accepts no new work on the old slot after this validated reload.
  write_upstream "$inactive" "$active"
  sleep "$DRAIN_SECONDS"
  atomic_link "$release" "$CURRENT_LINK"
  UPGRADE_READY_WAIT_SECONDS=$((SECONDS-start))
  trap - ERR
}

run_migration_phase() {
  local release="$1" serving="$2" target="$3" args=()
  [[ "${UPG01_MAINTENANCE_MODE:-false}" == "true" ]] && args+=(--maintenance)
  write_upgrade_status migrating "Applying and validating schema changes before cutover."
  if ! run_release_manage "$release" migrate \
    --serving-release "${serving:-none}" --target-release "$target" "${args[@]}"; then
    write_upgrade_status failed "Database migration failed; release was not activated. Verify DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, and DB_NAME in ${ENV_FILE}."
    echo "Database migration failed; release was not activated. Verify DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, and DB_NAME in ${ENV_FILE}." >&2
    return 1
  fi
}

is_additive_migration_only_release() {
  local old_revision="$1" target_revision="$2" status path extra metadata
  [[ -n "$old_revision" ]] || return 1
  git cat-file -e "${old_revision}^{commit}" 2>/dev/null || return 1
  git cat-file -e "${target_revision}^{commit}" 2>/dev/null || return 1
  git diff --quiet "$old_revision" "$target_revision" && return 1

  while IFS=$'\t' read -r status path extra; do
    # Deletions, renames, copies, and type changes are never additive. Checking
    # the status also prevents git-show attempts for paths absent from target.
    [[ "$status" == A || "$status" == M ]] || return 1
    [[ -z "$extra" ]] || return 1
    [[ "$path" == migrations/*.sql || "$path" == changes/*.json ]] || return 1
    if [[ "$path" == migrations/*.sql ]]; then
      metadata=$(git show "${target_revision}:${path}" 2>/dev/null) || return 1
      grep -Eiq '^--[[:space:]]*phase:[[:space:]]*expand[[:space:]]*$' <<<"$metadata" || return 1
      grep -Eiq '^--[[:space:]]*compatible-from:[[:space:]]*\*[[:space:]]*$' <<<"$metadata" || return 1
      grep -Eiq '^--[[:space:]]*compatible-to:[[:space:]]*\*[[:space:]]*$' <<<"$metadata" || return 1
    fi
  done < <(git diff --name-status "$old_revision" "$target_revision")
  return 0
}

command -v git >/dev/null && command -v curl >/dev/null && command -v nginx >/dev/null && command -v systemctl >/dev/null
cd "$PROJECT_ROOT"
validate_origin_remote "$(git config --get remote.origin.url)"
validate_required_configuration
UPGRADE_STARTED_AT=$(date --iso-8601=seconds)
PREVIOUS_RELEASE=$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)
git fetch --quiet origin main
TARGET_REVISION=$(git rev-parse 'origin/main^{commit}')
RELEASE_DIR="${RELEASE_ROOT}/${TARGET_REVISION}"

write_upgrade_status preparing "Preparing immutable release ${TARGET_REVISION}."
prepare_release "$TARGET_REVISION" "$RELEASE_DIR"
run_migration_phase "$RELEASE_DIR" "${PREVIOUS_RELEASE##*/}" "$TARGET_REVISION"
if is_additive_migration_only_release "${PREVIOUS_RELEASE##*/}" "$TARGET_REVISION"; then
  # Schema-only expands need no worker signal: the serving revision was
  # explicitly declared compatible and the database lock applied them once.
  atomic_link "$RELEASE_DIR" "$CURRENT_LINK"
  RESTART_MODE="migration-only"
  write_upgrade_status succeeded "Additive migration release ${TARGET_REVISION} applied; application workers were not reloaded."
  echo "Successfully applied migration-only release ${TARGET_REVISION}."
  exit 0
fi
run_rolling_restart "$TARGET_REVISION" "$RELEASE_DIR"
write_upgrade_status succeeded "Release ${TARGET_REVISION} is serving; previous release retained."
echo "Successfully deployed ${TARGET_REVISION} from ${RELEASE_DIR}."
