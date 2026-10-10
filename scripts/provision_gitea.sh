#!/usr/bin/env bash
# Idempotent Gitea setup for the RMM script library on Debian/Ubuntu install
# and upgrade paths (bare metal, VM or LXC).
#
# Gitea runs from the official binary as the "gitea" service account on
# 127.0.0.1:3000, and nginx serves it at /gitea on the portal's address. The
# first run creates an administrator, a private "rmm-scripts" repository and a
# token MyPortal uses to read scripts and create the script folders, and
# writes the GITEA_* settings MyPortal needs. Later runs move the binary to
# GITEA_VERSION and keep data, accounts and settings.
#
# Skipped when GITEA_PROVISION=false or GITEA_BASE_URL names another server.
#
# Usage: provision_gitea.sh ENV_FILE
set -euo pipefail

GITEA_VERSION="${MYPORTAL_GITEA_VERSION:-1.24.6}"
GITEA_DOWNLOAD_URL="${MYPORTAL_GITEA_DOWNLOAD_URL:-https://dl.gitea.com/gitea}"
GITEA_BIN="${MYPORTAL_GITEA_BIN:-/usr/local/bin/gitea}"
GITEA_HOME="${MYPORTAL_GITEA_HOME:-/var/lib/gitea}"
# Git otherwise searches the folders above Gitea's data for a repository, and a
# stray .git there (even /.git) stops Gitea starting.
GITEA_GIT_CEILING=$(dirname "$GITEA_HOME")
GITEA_CONFIG_DIR="${MYPORTAL_GITEA_CONFIG_DIR:-/etc/gitea}"
GITEA_UNIT="${MYPORTAL_GITEA_UNIT:-/etc/systemd/system/gitea.service}"
GITEA_CONFIG="${GITEA_CONFIG_DIR}/app.ini"
# Root-only: the administrator's sign-in, shown once by the installer.
GITEA_ADMIN_FILE="${GITEA_CONFIG_DIR}/admin-credentials"
GITEA_ACCOUNT="gitea"
GITEA_LOCAL_URL="http://127.0.0.1:3000"
GITEA_PUBLIC_PATH="/gitea"
GITEA_ADMIN_USER="myportal"
GITEA_ADMIN_EMAIL="scripts@myportal.localhost"
GITEA_REPOSITORY="rmm-scripts"

info() { printf '%s\n' "$*" >&2; }
warn() { printf 'Warning: %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Environment file (parsed as data: values can contain shell metacharacters)
# ---------------------------------------------------------------------------
env_value() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
from pathlib import Path

value = ""
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip().removeprefix("export ")
    if line.startswith("#") or "=" not in line:
        continue
    key, candidate = line.split("=", 1)
    if key.strip() != sys.argv[2]:
        continue
    candidate = candidate.strip()
    if candidate.startswith(("'", '"')):
        candidate = candidate[1:].split(candidate[0], 1)[0]
    else:
        candidate = candidate.split(" #", 1)[0].strip()
    value = candidate
print(value)
PY
}

env_default() {
  # env_default KEY VALUE: set KEY only when it is missing or empty, keeping
  # the file's other lines and permissions.
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys
from pathlib import Path

path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
indices = []
for index, raw in enumerate(lines):
    line = raw.strip().removeprefix("export ")
    if line.startswith("#") or "=" not in line:
        continue
    name, current = line.split("=", 1)
    if name.strip() != key:
        continue
    current = current.strip()
    if current.startswith(("'", '"')):
        current = current[1:].split(current[0], 1)[0]
    else:
        current = current.split(" #", 1)[0].strip()
    if current:
        raise SystemExit(0)
    indices.append(index)
for index in indices:
    lines[index] = f"{key}={value}"
if not indices:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

managed_gitea() {
  # True when this host should run Gitea for MyPortal.
  local provision base_url
  provision=$(env_value GITEA_PROVISION)
  case "${provision,,}" in false|0|no|off) return 1 ;; esac
  base_url=$(env_value GITEA_BASE_URL)
  [[ -z "$base_url" || "${base_url%/}" == "$GITEA_LOCAL_URL" ]]
}

# ---------------------------------------------------------------------------
# Binary, account and configuration
# ---------------------------------------------------------------------------
gitea_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64' ;;
    aarch64|arm64) printf 'arm64' ;;
    armv7l|armv7) printf 'arm-6' ;;
    *) return 1 ;;
  esac
}

installed_version() {
  [[ -x "$GITEA_BIN" ]] || return 0
  "$GITEA_BIN" --version 2>/dev/null | sed -n 's/^Gitea version \([0-9][^ ]*\).*/\1/p' | head -n1
}

install_binary() {
  # Downloads GITEA_VERSION and installs it only when its checksum matches.
  local arch name workdir expected actual
  arch=$(gitea_arch) || { warn "Gitea has no build for $(uname -m)."; return 1; }
  name="gitea-${GITEA_VERSION}-linux-${arch}"
  workdir=$(mktemp -d)
  info "Downloading Gitea ${GITEA_VERSION}…"
  if ! curl -fsSL --retry 3 -o "${workdir}/${name}" "${GITEA_DOWNLOAD_URL}/${GITEA_VERSION}/${name}" \
      || ! curl -fsSL --retry 3 -o "${workdir}/${name}.sha256" "${GITEA_DOWNLOAD_URL}/${GITEA_VERSION}/${name}.sha256"; then
    rm -rf "$workdir"
    warn "could not download Gitea ${GITEA_VERSION}."
    return 1
  fi
  expected=$(awk '{print $1; exit}' "${workdir}/${name}.sha256")
  actual=$(sha256sum "${workdir}/${name}" | awk '{print $1}')
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$expected" != "$actual" ]]; then
    rm -rf "$workdir"
    warn "the Gitea ${GITEA_VERSION} download failed its checksum; not installing it."
    return 1
  fi
  install -m 0755 "${workdir}/${name}" "$GITEA_BIN"
  rm -rf "$workdir"
}

ensure_account() {
  id -u "$GITEA_ACCOUNT" >/dev/null 2>&1 \
    || useradd --system --user-group --home-dir "$GITEA_HOME" --shell /usr/sbin/nologin "$GITEA_ACCOUNT"
  install -d -o "$GITEA_ACCOUNT" -g "$GITEA_ACCOUNT" -m 0750 \
    "$GITEA_HOME" "${GITEA_HOME}/custom" "${GITEA_HOME}/data" "${GITEA_HOME}/log"
  install -d -o root -g "$GITEA_ACCOUNT" -m 0770 "$GITEA_CONFIG_DIR"
}

root_url() {
  # Gitea's own address: the portal's public address plus /gitea/.
  local portal
  portal=$(env_value PORTAL_URL)
  if [[ "$portal" =~ ^https?://[^/]+ ]]; then
    printf '%s%s/' "${BASH_REMATCH[0]}" "$GITEA_PUBLIC_PATH"
  else
    printf 'http://%s%s/' "$(hostname -f 2>/dev/null || hostname)" "$GITEA_PUBLIC_PATH"
  fi
}

ini_set() {
  # ini_set SECTION KEY VALUE: prints "changed" when the file was modified.
  python3 - "$GITEA_CONFIG" "$1" "$2" "$3" <<'PY'
import re
import sys
from pathlib import Path

path, section, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
lines = path.read_text(encoding="utf-8").splitlines()
current = ""
start = end = None
for index, line in enumerate(lines):
    match = re.match(r"\s*\[(.+?)\]\s*$", line)
    if match:
        if current == section:
            end = index
            break
        current = match.group(1)
        if current == section:
            start = index
if start is None:
    lines += ["", f"[{section}]", f"{key} = {value}"]
else:
    end = len(lines) if end is None else end
    for index in range(start + 1, end):
        if re.match(rf"\s*{re.escape(key)}\s*=", lines[index]):
            if lines[index].split("=", 1)[1].strip() == value:
                raise SystemExit(0)
            lines[index] = f"{key} = {value}"
            break
    else:
        lines.insert(end, f"{key} = {value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("changed")
PY
}

write_config() {
  # Written once; later runs keep ROOT_URL in step with PORTAL_URL and apply
  # the sign-in settings (configure_sign_in).
  [[ -f "$GITEA_CONFIG" ]] && return 0
  local secret_key internal_token jwt_secret tmp
  secret_key=$("$GITEA_BIN" generate secret SECRET_KEY)
  internal_token=$("$GITEA_BIN" generate secret INTERNAL_TOKEN)
  jwt_secret=$("$GITEA_BIN" generate secret JWT_SECRET)
  tmp=$(mktemp "${GITEA_CONFIG}.XXXXXX")
  cat >"$tmp" <<INI
; Gitea for the MyPortal RMM script library. Written by
; scripts/provision_gitea.sh, which afterwards only changes [server] ROOT_URL
; and the MyPortal sign-in settings.
APP_NAME = MyPortal scripts
RUN_USER = ${GITEA_ACCOUNT}
RUN_MODE = prod
WORK_PATH = ${GITEA_HOME}

[server]
PROTOCOL = http
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = $(root_url)
; Links follow the address the browser used (nginx forwards Host and scheme).
PUBLIC_URL_DETECTION = auto
DISABLE_SSH = true
START_SSH_SERVER = false
LFS_START_SERVER = false
OFFLINE_MODE = true

[database]
DB_TYPE = sqlite3
PATH = ${GITEA_HOME}/data/gitea.db

[repository]
ROOT = ${GITEA_HOME}/data/repositories
DEFAULT_BRANCH = main
DEFAULT_PRIVATE = private

[security]
INSTALL_LOCK = true
SECRET_KEY = ${secret_key}
INTERNAL_TOKEN = ${internal_token}

[oauth2]
JWT_SECRET = ${jwt_secret}

[service]
DISABLE_REGISTRATION = true
REQUIRE_SIGNIN_VIEW = true
DEFAULT_KEEP_EMAIL_PRIVATE = true

[openid]
ENABLE_OPENID_SIGNIN = false
ENABLE_OPENID_SIGNUP = false

[mailer]
ENABLED = false

[cron.update_checker]
ENABLED = false

[log]
MODE = console
LEVEL = Info
INI
  chown "${GITEA_ACCOUNT}:${GITEA_ACCOUNT}" "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$GITEA_CONFIG"
  printf 'changed'
}

configure_sign_in() {
  # MyPortal sign-in: nginx sends who is signed in to MyPortal in these
  # headers, which Gitea accepts only from this host (it listens on
  # 127.0.0.1). Prints "changed" when app.ini was modified.
  local setting
  for setting in \
    "service ENABLE_REVERSE_PROXY_AUTHENTICATION true" \
    "service ENABLE_REVERSE_PROXY_AUTO_REGISTRATION true" \
    "service ENABLE_REVERSE_PROXY_EMAIL true" \
    "service ENABLE_REVERSE_PROXY_FULL_NAME true" \
    "security REVERSE_PROXY_AUTHENTICATION_USER X-WEBAUTH-USER" \
    "security REVERSE_PROXY_AUTHENTICATION_EMAIL X-WEBAUTH-EMAIL" \
    "security REVERSE_PROXY_AUTHENTICATION_FULL_NAME X-WEBAUTH-FULLNAME" \
    "security REVERSE_PROXY_TRUSTED_PROXIES 127.0.0.0/8,::1/128"; do
    # shellcheck disable=SC2086 # three words: section, key, value
    ini_set $setting
  done
}

write_unit() {
  # Prints "changed" when the unit was installed or replaced.
  local tmp
  tmp=$(mktemp)
  cat >"$tmp" <<UNIT
[Unit]
Description=Gitea for the MyPortal script library
After=network.target

[Service]
Type=simple
User=${GITEA_ACCOUNT}
Group=${GITEA_ACCOUNT}
WorkingDirectory=${GITEA_HOME}
Environment=USER=${GITEA_ACCOUNT} HOME=${GITEA_HOME} GITEA_WORK_DIR=${GITEA_HOME}
Environment=GIT_CEILING_DIRECTORIES=${GITEA_GIT_CEILING}
ExecStart=${GITEA_BIN} web --config ${GITEA_CONFIG}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
UNIT
  if cmp -s "$tmp" "$GITEA_UNIT"; then
    rm -f "$tmp"
    return 0
  fi
  install -m 0644 "$tmp" "$GITEA_UNIT"
  rm -f "$tmp"
  printf 'changed'
}

wait_for_gitea() {
  local waited=0
  until curl -fsS --max-time 5 "${GITEA_LOCAL_URL}/api/healthz" >/dev/null 2>&1; do
    ((waited < 90)) || return 1
    sleep 2
    waited=$((waited + 2))
  done
}

# ---------------------------------------------------------------------------
# Administrator, repository and MyPortal's token
# ---------------------------------------------------------------------------
gitea_cli() {
  ( cd "$GITEA_HOME" && runuser -u "$GITEA_ACCOUNT" -- env HOME="$GITEA_HOME" GITEA_WORK_DIR="$GITEA_HOME" \
    GIT_CEILING_DIRECTORIES="$GITEA_GIT_CEILING" \
    "$GITEA_BIN" --config "$GITEA_CONFIG" "$@" )
}

gitea_api() {
  # gitea_api METHOD PATH [JSON]: prints the HTTP status. Credentials go to
  # curl as a config file on stdin, so they never appear in process arguments.
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-sS -o /dev/null -w '%{http_code}' -X "$method" -K -)
  [[ -z "$body" ]] || args+=(-H 'Content-Type: application/json' --data "$body")
  curl "${args[@]}" "${GITEA_LOCAL_URL}/api/v1${path}" || printf '000'
}

curl_auth() {
  # Curl config lines: a token header, or basic auth from the credentials file.
  if [[ -n "${1:-}" ]]; then
    printf 'header = "Authorization: token %s"\n' "$1"
  else
    local password
    password=$(sed -n 's/^password=//p' "$GITEA_ADMIN_FILE" 2>/dev/null | head -n1)
    password=${password//\\/\\\\}
    printf 'user = "%s:%s"\n' "$GITEA_ADMIN_USER" "${password//\"/\\\"}"
  fi
}

ensure_admin() {
  local output password
  # Captured first: "grep -q" exiting early would fail the pipeline (pipefail).
  output=$(gitea_cli admin user list --admin 2>/dev/null || true)
  if awk 'NR > 1 {print $2}' <<<"$output" | grep -x "$GITEA_ADMIN_USER" >/dev/null; then
    return 0
  fi
  info "Creating the Gitea administrator '${GITEA_ADMIN_USER}'…"
  output=$(gitea_cli admin user create --admin --username "$GITEA_ADMIN_USER" --email "$GITEA_ADMIN_EMAIL" \
    --random-password --must-change-password=false)
  password=$(printf '%s\n' "$output" | sed -n "s/.*generated random password is '\(.*\)'.*/\1/p" | head -n1)
  [[ -n "$password" ]] || { warn "Gitea did not report the administrator's password."; return 1; }
  ( umask 077; printf 'url=%s\nusername=%s\npassword=%s\n' "$(root_url)" "$GITEA_ADMIN_USER" "$password" >"$GITEA_ADMIN_FILE" )
  chmod 0600 "$GITEA_ADMIN_FILE"
}

create_token() {
  # Prints a new token for MyPortal: it reads scripts and creates the folders.
  gitea_cli admin user generate-access-token --username "$GITEA_ADMIN_USER" \
    --token-name "myportal-$(date -u +%Y%m%d%H%M%S)" --scopes write:repository \
    | grep -oE '[0-9a-f]{40}' | tail -n1
}

ensure_repository() {
  local token="$1" status
  status=$(curl_auth "$token" | gitea_api GET "/repos/${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}")
  [[ "$status" != 200 ]] || return 0
  info "Creating the Gitea repository ${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}…"
  status=$(curl_auth | gitea_api POST /user/repos \
    "{\"name\":\"${GITEA_REPOSITORY}\",\"description\":\"Scripts MyPortal runs on devices\",\"private\":true,\"auto_init\":true,\"default_branch\":\"main\",\"readme\":\"Default\"}")
  [[ "$status" == 201 || "$status" == 409 ]] || {
    warn "could not create ${GITEA_ADMIN_USER}/${GITEA_REPOSITORY} in Gitea (HTTP ${status}); create it, then sync scripts."
    return 1
  }
}

connect_myportal() {
  # Gives MyPortal its token and repository once; later runs keep them.
  local token
  token=$(env_value GITEA_API_TOKEN)
  if [[ -z "$token" ]]; then
    ensure_admin || return 1
    token=$(create_token)
    [[ -n "$token" ]] || { warn "could not create a Gitea token for MyPortal."; return 1; }
    ensure_repository "$token" || true
    env_default GITEA_API_TOKEN "$token"
  fi
  env_default GITEA_BASE_URL "$GITEA_LOCAL_URL"
  env_default GITEA_PUBLIC_URL "$GITEA_PUBLIC_PATH"
  env_default GITEA_SCRIPTS_REPOSITORY "${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}"
  env_default GITEA_SCRIPTS_BRANCH main
}

main() {
  ENV_FILE="${1:?Usage: provision_gitea.sh ENV_FILE}"
  managed_gitea || exit 0
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    warn "Gitea needs systemd; skipping it. Set GITEA_* in ${ENV_FILE} to use another Gitea server."
    exit 1
  fi
  command -v git >/dev/null 2>&1 \
    || env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null

  local changed="" current
  current=$(installed_version)
  if [[ "$current" != "$GITEA_VERSION" ]]; then
    if install_binary; then
      changed=yes
    elif [[ -z "$current" ]]; then
      exit 1
    else
      warn "keeping Gitea ${current}."
    fi
  fi
  ensure_account
  changed+=$(write_config)
  changed+=$(ini_set server ROOT_URL "$(root_url)")
  changed+=$(configure_sign_in)
  changed+=$(write_unit)

  systemctl daemon-reload
  if [[ -n "$changed" ]]; then
    systemctl enable gitea.service >/dev/null 2>&1
    systemctl restart gitea.service
  else
    systemctl enable --now gitea.service >/dev/null 2>&1
  fi
  wait_for_gitea || { warn "Gitea did not start; see 'journalctl -u gitea'."; exit 1; }
  connect_myportal
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
