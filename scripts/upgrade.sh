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

write_upstream_file() {
  local active="$1" inactive="$2" tmp="${UPSTREAM_FILE}.new.$$"
  mkdir -p "$(dirname "$UPSTREAM_FILE")"
  printf 'server 127.0.0.1:%s max_fails=1 fail_timeout=5s;\nserver 127.0.0.1:%s down;\n' \
    "$(instance_port "$active")" "$(instance_port "$inactive")" >"$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$UPSTREAM_FILE"
}

write_upstream() {
  write_upstream_file "$1" "$2"
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

install_blue_green_service_unit() {
  local release="$1"
  local source_unit="${release}/deploy/systemd/myportal@.service"
  local installed_unit="/etc/systemd/system/myportal@.service"

  if [[ ! -r "$source_unit" ]]; then
    echo "Blue/green systemd unit is missing from release: ${source_unit}" >&2
    return 1
  fi
  if [[ "${EUID:-$(id -u)}" != 0 ]]; then
    echo "Blue/green service setup requires root. Re-run the upgrade with sudo." >&2
    return 1
  fi

  # Older installations have only myportal.service. Install (or update) the
  # instance template before attempting to start either deployment slot.
  install -m 0644 "$source_unit" "$installed_unit"
  systemctl daemon-reload
  # A duplicated unit suffix creates the template instance "blue.service" or
  # "green.service". The unit intentionally rejects that invalid instance with
  # EX_USAGE (64), so remove any stale malformed units before enabling the
  # canonical blue/green names.
  systemctl disable --now myportal@blue.service.service myportal@green.service.service >/dev/null 2>&1 || true
  systemctl reset-failed myportal@blue.service.service myportal@green.service.service >/dev/null 2>&1 || true
  systemctl enable myportal@blue.service myportal@green.service >/dev/null
  if ! systemctl cat myportal@.service >/dev/null; then
    echo "Unable to register myportal@.service; check systemd and ${installed_unit}." >&2
    return 1
  fi
}

install_blue_green_nginx_config() {
  local release="$1" active="$2" inactive="$3"
  local source_config="${release}/deploy/nginx/myportal-bluegreen.conf"
  local available_dir="/etc/nginx/sites-available"
  local enabled_dir="/etc/nginx/sites-enabled"
  local installed_config

  if [[ ! -r "$source_config" ]]; then
    echo "Blue/green nginx configuration is missing from release: ${source_config}" >&2
    return 1
  fi
  if [[ "${EUID:-$(id -u)}" != 0 ]]; then
    echo "Blue/green nginx setup requires root. Re-run the upgrade with sudo." >&2
    return 1
  fi

  # Debian-family packages use sites-available/sites-enabled, while other
  # nginx packages load conf.d directly. Install into the layout nginx already
  # provides instead of requiring a manual proxy setup after the workers start.
  if [[ -d "$available_dir" && -d "$enabled_dir" ]]; then
    installed_config="${available_dir}/myportal.conf"
    install -m 0644 "$source_config" "$installed_config"
    ln -sfn "$installed_config" "${enabled_dir}/myportal.conf"
  else
    installed_config="/etc/nginx/conf.d/myportal.conf"
    install -d -m 0755 "$(dirname "$installed_config")"
    install -m 0644 "$source_config" "$installed_config"
  fi

  # The include is mandatory for nginx -t. Point it at the already validated
  # candidate so a first-time nginx start cannot expose a dead legacy backend.
  write_upstream_file "$active" "$inactive"
  nginx -t
  systemctl enable --now nginx
}

make_release_service_readable() {
  local release="$1"
  # Releases are commonly prepared by root with umask 027. The service runs as
  # the unprivileged myportal user, so every directory must be traversable and
  # regular files must be readable. Do not follow symlinks: in particular, the
  # protected environment file must retain its existing permissions.
  find "$release" -type d -exec chmod a+rx,a-w {} +
  find "$release" -type f -exec chmod a+rX,a-w {} +
}

prepare_shared_uploads() {
  local shared legacy name
  for name in private_uploads uploads; do
    shared="${SHARED_ROOT}/${name}"
    if [[ "$name" == private_uploads ]]; then
      legacy="${PROJECT_ROOT}/private_uploads"
    else
      legacy="${PROJECT_ROOT}/app/static/uploads"
    fi
    if [[ ! -d "$shared" ]]; then
      install -d -m 0750 -o myportal -g myportal "$shared"
      # Seed persistent storage for installations upgrading from the original
      # single-checkout layout. Never remove or replace the legacy data.
      if [[ -d "$legacy" ]]; then
        cp -a "$legacy"/. "$shared"/
      fi
    fi
    # Repair both ownership and owner permissions left by immutable-release
    # preparation or interrupted legacy deployments. chown alone does not make
    # a root-created 0555 directory writable by its new owner.
    chown -R myportal:myportal "$shared"
    find "$shared" -type d -exec chmod u+rwx {} +
  done
}

link_shared_uploads() {
  local release="$1"
  mkdir -p "${release}/app/static"
  rm -rf "${release}/private_uploads" "${release}/app/static/uploads"
  ln -s "${SHARED_ROOT}/private_uploads" "${release}/private_uploads"
  ln -s "${SHARED_ROOT}/uploads" "${release}/app/static/uploads"
  # The target is the writable data store, but keep the link metadata owned by
  # the service account as well so ownership checks do not report these paths
  # as root-owned. -h prevents chown from dereferencing the links.
  chown -h myportal:myportal "${release}/private_uploads" "${release}/app/static/uploads"
}

validate_release_uploads() {
  local release="$1" path expected
  while IFS='|' read -r path expected; do
    if [[ ! -L "$path" || "$(readlink -f "$path" 2>/dev/null || true)" != "$expected" ]]; then
      echo "Release upload path is not linked to persistent storage: ${path}" >&2
      return 1
    fi
    if ! runuser --user myportal -- test -w "$path"; then
      echo "Release upload path is not writable by the myportal service account: ${path}" >&2
      return 1
    fi
  done <<EOF
${release}/private_uploads|${SHARED_ROOT}/private_uploads
${release}/app/static/uploads|${SHARED_ROOT}/uploads
EOF
}

repair_assigned_release_uploads() {
  local instance release path expected
  for instance in blue green; do
    release=$(readlink -f "${INSTANCE_ROOT}/${instance}" 2>/dev/null || true)
    [[ -n "$release" && -d "$release" && "$release" != "$RELEASE_DIR" ]] || continue

    chmod u+w "$release" "${release}/app" "${release}/app/static"
    while IFS='|' read -r path expected; do
      if [[ -d "$path" && ! -L "$path" ]]; then
        # Releases made by the older updater stored uploads locally. Preserve
        # files that are not already in shared storage before replacing the
        # directory; -n prevents an old slot overwriting newer shared files.
        cp -a -n "$path"/. "$expected"/
      fi
      rm -rf "$path"
      ln -s "$expected" "$path"
      chown -h myportal:myportal "$path"
    done <<EOF
${release}/private_uploads|${SHARED_ROOT}/private_uploads
${release}/app/static/uploads|${SHARED_ROOT}/uploads
EOF
    chown -R myportal:myportal "${SHARED_ROOT}/private_uploads" "${SHARED_ROOT}/uploads"
    find "${SHARED_ROOT}/private_uploads" "${SHARED_ROOT}/uploads" -type d -exec chmod u+rwx {} +
    make_release_service_readable "$release"
    validate_release_uploads "$release"
  done
}

release_runtime_ready() {
  local release="$1"
  [[ -x "${release}/.venv/bin/python" ]] || return 1
  # The systemd unit launches uvicorn as a module through this interpreter and
  # deliberately does not depend on a generated console-script shebang.
  "${release}/.venv/bin/python" -c 'import uvicorn' >/dev/null 2>&1
}

prepare_release() {
  local revision="$1" release="$2" staging
  staging="${release}.staging.$$"
  mkdir -p "$RELEASE_ROOT" "$INSTANCE_ROOT" "$SHARED_ROOT/state" "$SHARED_ROOT/data"
  # The service needs to traverse deployment-owned parents to reach both the
  # instance symlink and its immutable release target.
  chmod a+rx "$RELEASE_ROOT" "$INSTANCE_ROOT" "$SHARED_ROOT"
  prepare_shared_uploads
  if [[ -e "$release" ]]; then
    # A previous attempt may have prepared this revision with missing or stale
    # configuration. Refresh only the symlink; never copy or regenerate secrets.
    if [[ -f "$ENV_FILE" && "$(readlink "$release/.env" 2>/dev/null || true)" != "$ENV_FILE" ]]; then
      ln -sfn "$ENV_FILE" "$release/.env"
    fi
    chmod u+w "$release" "${release}/app" "${release}/app/static"
    link_shared_uploads "$release"
    # Older scripts moved a completed virtualenv from a temporary directory,
    # leaving console-script shebangs pointed at a path that no longer exists.
    # Rebuild only an unusable runtime; never mutate a healthy active release.
    if ! release_runtime_ready "$release"; then
      chmod -R u+w "$release"
      rm -rf "${release}/.venv"
      if ! install_dependencies "$release"; then
        make_release_service_readable "$release"
        return 1
      fi
    fi
    # Repair releases prepared with root-only traversal permissions before
    # retrying their service startup.
    make_release_service_readable "$release"
    validate_release_uploads "$release"
    return 0
  fi
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
  link_shared_uploads "$staging"
  # Publish the code path before creating its virtualenv. Entry-point scripts
  # embed an absolute interpreter path and break if the venv is subsequently
  # renamed from the staging path to the release path.
  mv "$staging" "$release"
  if ! install_dependencies "$release"; then
    rm -rf "$release"
    return 1
  fi
  make_release_service_readable "$release"
  validate_release_uploads "$release"
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
  local old_active="$1" new_instance="$2" old_instance_release="$3" upstream_switched="$4"
  echo "Deployment failed; restoring ${old_active}." >&2
  if [[ -n "$old_instance_release" && -d "$old_instance_release" ]]; then
    atomic_link "$old_instance_release" "$INSTANCE_ROOT/$new_instance"
    systemctl restart "myportal@${new_instance}.service" >/dev/null 2>&1 || true
  fi
  # A startup failure happens before nginx is changed. Do not replace a legacy
  # installation's working upstream with a blue slot that has never existed.
  [[ "$upstream_switched" == true ]] && write_upstream "$old_active" "$new_instance" || true
  [[ -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE" ]] && atomic_link "$PREVIOUS_RELEASE" "$CURRENT_LINK"
  write_upgrade_status failed "Cutover failed; previous release and upstream restored."
}

run_rolling_restart() {
  local revision="$1" release="$2" active inactive old_inactive upstream_switched=false start=$SECONDS
  active=$(read_active); [[ "$active" == blue ]] && inactive=green || inactive=blue
  old_inactive=$(readlink -f "$INSTANCE_ROOT/$inactive" 2>/dev/null || true)
  trap 'rollback "$active" "$inactive" "$old_inactive" "$upstream_switched"' ERR

  # Only the non-serving slot changes during preparation and validation.
  atomic_link "$release" "$INSTANCE_ROOT/$inactive"
  systemctl restart "myportal@${inactive}.service"
  wait_for_version "$(instance_port "$inactive")" "$revision"
  smoke_test "$(instance_port "$inactive")" "$revision"

  # Install and start the public listener only after its first backend has
  # passed readiness and smoke checks. This also promotes legacy deployments
  # whose workers existed but whose blue/green nginx site was never enabled.
  install_blue_green_nginx_config "$release" "$inactive" "$active"

  # nginx accepts no new work on the old slot after this validated reload.
  write_upstream "$inactive" "$active"
  upstream_switched=true
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
# The inactive slot may still point at a release produced before persistent
# upload links were introduced. Repair both assigned releases before systemd
# can start or roll back either one; otherwise an old worker loops while trying
# to create private_uploads inside its read-only release directory.
repair_assigned_release_uploads
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
install_blue_green_service_unit "$RELEASE_DIR"
run_rolling_restart "$TARGET_REVISION" "$RELEASE_DIR"
write_upgrade_status succeeded "Release ${TARGET_REVISION} is serving; previous release retained."
echo "Successfully deployed ${TARGET_REVISION} from ${RELEASE_DIR}."
