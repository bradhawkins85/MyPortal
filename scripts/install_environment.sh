#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_TEMPLATE="${PROJECT_ROOT}/.env.example"
VENV_DIR="${PROJECT_ROOT}/.venv"

# Production layout. These match the defaults used by scripts/upgrade.sh,
# deploy/systemd/myportal@.service and deploy/nginx/myportal-bluegreen.conf.
SERVICE_USER="myportal"
PRODUCTION_ENV_FILE="/etc/myportal.env"
DEPLOY_ROOT="/opt/myportal"
UPDATE_CRON_FILE="/etc/cron.d/myportal-update"

usage() {
  cat <<'USAGE'
Usage: install_environment.sh <environment>

Prepare a MyPortal installation on a Debian/Ubuntu host (bare metal, VM or
LXC). Supported environments are:
  production   Must run as root. Installs system packages (MariaDB, nginx,
               WeasyPrint libraries, baresip), creates the "myportal" service
               account and /etc/myportal.env, then performs the first
               immutable blue/green deployment with scripts/upgrade.sh. The
               portal is served by nginx on port 80.
  development  Installs dependencies in editable mode in <checkout>/.venv,
               provisions an isolated local "myportal_dev" database, applies
               migrations and, when run with sudo on a systemd host, starts
               the myportal-dev.service unit on port 8000.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

ENVIRONMENT="$1"
case "$ENVIRONMENT" in
  production|development)
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "Error: Unsupported environment '${ENVIRONMENT}'." >&2
    usage >&2
    exit 1
    ;;
esac

if [[ "$ENVIRONMENT" == "production" ]]; then
  ENV_FILE="$PRODUCTION_ENV_FILE"
else
  ENV_FILE="${PROJECT_ROOT}/.env"
fi

is_root() { [[ "${EUID:-$(id -u)}" == "0" ]]; }

if [[ "$ENVIRONMENT" == "production" ]] && ! is_root; then
  echo "Error: the production installer must run as root (use sudo)." >&2
  exit 1
fi

select_system_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$(command -v python3)"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s' "$(command -v python)"
    return
  fi
  printf ''
}

SYSTEM_PYTHON=$(select_system_python)

if [[ -z "$SYSTEM_PYTHON" ]]; then
  echo "Error: Python 3 is required to run the installer." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

APT_UPDATED=false

run_privileged() {
  # Elevate with sudo for a development install started by an unprivileged user.
  if is_root; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Error: root privileges are required to run: $*" >&2
    exit 1
  fi
}

apt_update_once() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Error: apt-get not found. MyPortal's installers support Debian and Ubuntu hosts." >&2
    exit 1
  fi
  # Fresh containers and minimal images ship without package lists, so refresh
  # them before checking which packages are available.
  if [[ "$APT_UPDATED" != true ]]; then
    run_privileged apt-get update -qq || echo "Warning: apt-get update failed." >&2
    APT_UPDATED=true
  fi
}

apt_install() {
  apt_update_once
  run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}

apt_package_available() {
  command -v apt-cache >/dev/null 2>&1 && [[ -n "$(apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/ {print $2}' | grep -v '(none)')" ]]
}

install_system_packages() {
  apt_update_once
  local py_version
  py_version=$("$SYSTEM_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

  # Debian and Ubuntu split ensurepip out of the standard library. The venv
  # module itself is always importable, so test for ensurepip: without it
  # `python3 -m venv` creates an environment that has no pip.
  local -a packages=()
  if ! "$SYSTEM_PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
    if apt_package_available "python${py_version}-venv"; then
      packages+=("python${py_version}-venv")
    else
      packages+=(python3-venv)
    fi
  fi

  # git/curl/flock/runuser are used by the upgrade coordinator; the pango,
  # harfbuzz and libmagic runtime libraries are loaded by WeasyPrint (PDF
  # rendering) and python-magic.
  packages+=(ca-certificates git curl util-linux libpango-1.0-0 libpangoft2-1.0-0 libmagic1)
  if apt_package_available libharfbuzz-subset0; then
    packages+=(libharfbuzz-subset0)
  fi
  if [[ "$ENVIRONMENT" == "production" ]]; then
    packages+=(nginx cron)
  fi

  echo "Installing system packages: ${packages[*]}" >&2
  apt_install "${packages[@]}"

  if ! "$SYSTEM_PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
    echo "Error: Python ensurepip is still unavailable; install python${py_version}-venv and rerun the installer." >&2
    exit 1
  fi
}

install_sip_client() {
  if command -v baresip >/dev/null 2>&1; then
    echo "SIP client (baresip) is already installed." >&2
    return
  fi
  echo "Installing server SIP client (baresip)…" >&2
  apt_install baresip
  command -v baresip >/dev/null 2>&1 || { echo "Error: baresip installation failed." >&2; exit 1; }
}

# ---------------------------------------------------------------------------
# Environment file
# ---------------------------------------------------------------------------

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi

  local legacy_env="${PROJECT_ROOT}/.env"
  if [[ "$ENVIRONMENT" == "production" && -f "$legacy_env" ]]; then
    # Installations created by the previous installer kept their configuration
    # (including live secrets and database credentials) in the checkout.
    # Carry it over unchanged rather than generating new keys.
    install -m 0640 "$legacy_env" "$ENV_FILE"
    echo "Created ${ENV_FILE} from the existing ${legacy_env}." >&2
    return
  fi

  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    echo "Error: ${ENV_TEMPLATE} template not found." >&2
    exit 1
  fi

  install -m 0600 "$ENV_TEMPLATE" "$ENV_FILE"
  echo "Created ${ENV_FILE} from template." >&2

  if [[ "$ENVIRONMENT" == "development" ]]; then
    # Keep a development database separate from a production database that
    # may share this host's MariaDB server.
    set_env_value DB_NAME myportal_dev
    set_env_value DB_USER myportal_dev
    set_env_value SYSTEMD_SERVICE_NAME myportal-dev
    # The development service runs a single checkout; there is no immutable
    # release for the auto-update wrapper to deploy.
    set_env_value UVICORN_AUTO_UPDATE_ENABLED false
    # /var/log/myportal belongs to the production service account; log to the
    # console (and so the journal) instead.
    set_env_value APP_LOG_PATH ""
  fi
}

set_env_value() {
  # Set KEY=VALUE, replacing an existing assignment or appending a new one.
  ENV_SET_KEY="$1" ENV_SET_VALUE="$2" ENV_SET_FILE="$ENV_FILE" "$SYSTEM_PYTHON" - <<'PY'
import os
from pathlib import Path

env_path = Path(os.environ["ENV_SET_FILE"])
key = os.environ["ENV_SET_KEY"]
value = os.environ["ENV_SET_VALUE"]
content = env_path.read_text(encoding="utf-8")
lines = content.splitlines()
for index, raw_line in enumerate(lines):
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        continue
    if raw_line.split("=", 1)[0].strip() == key:
        lines[index] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

ensure_env_default() {
  local key="$1"
  local default_value="$2"

  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi

  ENV_DEFAULT_KEY="$key" \
    ENV_DEFAULT_VALUE="$default_value" \
    ENV_DEFAULT_FILE="$ENV_FILE" \
    "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

env_path = Path(os.environ["ENV_DEFAULT_FILE"])
key = os.environ["ENV_DEFAULT_KEY"]
default = os.environ["ENV_DEFAULT_VALUE"]

existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
for raw_line in existing.splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        continue
    name, _ = raw_line.split("=", 1)
    if name.strip() == key:
        break
else:
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    env_path.write_text(existing + f"{suffix}{key}={default}\n", encoding="utf-8")
PY
}

ensure_env_secret() {
  # Replace weak or placeholder values for the given key with a freshly
  # generated cryptographically-random string. Existing non-placeholder
  # values are left untouched so redeploying never rotates live secrets.
  # With "placeholder-only", a short but deliberately chosen value (such as
  # an existing database password) is also preserved.
  local key="$1"
  local byte_length="${2:-48}"
  local mode="${3:-weak}"

  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi

  ENV_SECRET_KEY="$key" \
    ENV_SECRET_BYTES="$byte_length" \
    ENV_SECRET_MODE="$mode" \
    ENV_SECRET_FILE="$ENV_FILE" \
    "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
import secrets
from pathlib import Path

env_path = Path(os.environ["ENV_SECRET_FILE"])
key = os.environ["ENV_SECRET_KEY"]
byte_length = int(os.environ.get("ENV_SECRET_BYTES", "48"))
placeholder_only = os.environ.get("ENV_SECRET_MODE") == "placeholder-only"

PLACEHOLDER = {
    "",
    "change-me",
    "changeme",
    "change_me",
    "please-change",
    "replace-me",
    "secret",
    "password",
    "strong-password",
}


def looks_weak(value: str) -> bool:
    stripped = value.strip().strip('"').strip("'")
    if stripped.lower() in PLACEHOLDER:
        return True
    if not placeholder_only and len(stripped) < 24:
        return True
    return False


content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
lines = content.splitlines()
updated = False
found = False
for index, raw_line in enumerate(lines):
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        continue
    name, value = raw_line.split("=", 1)
    if name.strip() != key:
        continue
    found = True
    if looks_weak(value):
        new_value = secrets.token_urlsafe(byte_length)
        lines[index] = f"{key}={new_value}"
        updated = True
        print(f"Generated new value for {key}.")
    break

if not found:
    new_value = secrets.token_urlsafe(byte_length)
    suffix = "" if not content or content.endswith("\n") else "\n"
    env_path.write_text(content + f"{suffix}{key}={new_value}\n", encoding="utf-8")
    print(f"Appended new value for {key}.")
elif updated:
    trailing = "\n" if content.endswith("\n") else ""
    env_path.write_text("\n".join(lines) + trailing, encoding="utf-8")
PY
}

secure_env_file_permissions() {
  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi
  if [[ "$ENVIRONMENT" == "production" ]]; then
    # systemd reads the file as root; the application (running as the service
    # account) also reads it through each release's .env symlink.
    chown "root:${SERVICE_USER}" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
  else
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    if is_root && [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
      chown "${SUDO_USER}:$(id -gn "$SUDO_USER")" "$ENV_FILE"
    fi
  fi
}

read_env_value() {
  local key="$1"
  local default_value="${2:-}"

  if [[ ! -f "$ENV_FILE" ]]; then
    printf '%s' "$default_value"
    return
  fi

  ENV_LOOKUP_KEY="$key" \
    ENV_LOOKUP_DEFAULT="$default_value" \
    ENV_LOOKUP_FILE="$ENV_FILE" \
    "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

key = os.environ["ENV_LOOKUP_KEY"]
default = os.environ.get("ENV_LOOKUP_DEFAULT", "")
env_path = Path(os.environ["ENV_LOOKUP_FILE"])

if not env_path.exists():
    print(default)
    raise SystemExit

for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        continue
    name, value = raw_line.split("=", 1)
    if name.strip() != key:
        continue
    value = value.strip()
    if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    print(value)
    break
else:
    print(default)
PY
}

# ---------------------------------------------------------------------------
# Production: service account, directories, first deployment, update cron
# ---------------------------------------------------------------------------

ensure_service_account() {
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    return
  fi
  local nologin
  nologin=$(command -v nologin 2>/dev/null || echo /usr/sbin/nologin)
  useradd --system --user-group --home-dir /nonexistent --no-create-home \
    --shell "$nologin" "$SERVICE_USER"
  echo "Created service account '${SERVICE_USER}'." >&2
}

prepare_production_directories() {
  # Created with explicit modes: upgrade.sh runs with umask 027, which would
  # otherwise leave /opt/myportal untraversable by the service account.
  install -d -m 0755 "$DEPLOY_ROOT"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/log/myportal
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/lib/myportal
  # Root-only working area for the update coordinator (plan, locks, captured
  # output). It must not be writable by the service account.
  install -d -m 0700 -o root -g root /var/lib/myportal-updater
}

check_control_checkout() {
  local origin
  origin=$(git -C "$PROJECT_ROOT" config --get remote.origin.url 2>/dev/null || true)
  if [[ -z "$origin" ]]; then
    echo "Error: ${PROJECT_ROOT} is not a git clone with an 'origin' remote." >&2
    echo "Clone the repository (for example into ${DEPLOY_ROOT}/control) and rerun the installer." >&2
    exit 1
  fi
  case "$PROJECT_ROOT" in
    /home/*|/root/*)
      # myportal@.service sets ProtectHome=true, so the application cannot
      # read a checkout here to look for updates from the admin UI.
      echo "Warning: the control checkout ${PROJECT_ROOT} is under a home directory." >&2
      echo "Deployments will work, but the admin UI cannot check for updates. Clone into ${DEPLOY_ROOT}/control instead." >&2
      ;;
  esac
}

run_first_deployment() {
  echo "Deploying the latest release with scripts/upgrade.sh…" >&2
  MYPORTAL_ENV_FILE="$ENV_FILE" "${SCRIPT_DIR}/upgrade.sh"
}

install_update_cron() {
  cat >"$UPDATE_CRON_FILE" <<CRON
# Installed by scripts/install_production.sh. Applies updates requested from
# the MyPortal admin UI (the application writes the system_update flag).
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Root output goes to a root-owned log: /var/log/myportal belongs to the
# service account, which could otherwise redirect this append with a symlink.
* * * * * root ${SCRIPT_DIR}/process_update_flag.sh >> /var/log/myportal-updater.log 2>&1
CRON
  chmod 644 "$UPDATE_CRON_FILE"
  # Upgrade output can name hosts and paths; keep the log private to root.
  [[ -e /var/log/myportal-updater.log ]] || install -m 0600 -o root -g root /dev/null /var/log/myportal-updater.log
  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    systemctl enable --now cron >/dev/null 2>&1 || true
  fi
}

# ---------------------------------------------------------------------------
# Development: virtualenv, migrations, optional systemd unit
# ---------------------------------------------------------------------------

as_checkout_owner() {
  # When a developer runs the installer with sudo, build the virtualenv and run
  # migrations as that developer so the checkout (including .venv, egg-info and
  # __pycache__) stays owned by them and later git/pip commands work unprivileged.
  if is_root && [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    runuser -u "$SUDO_USER" -- "$@"
  else
    "$@"
  fi
}

ensure_virtualenv() {
  if [[ -d "$VENV_DIR" ]] && is_root && [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    # Earlier installer versions ran pip as root inside the checkout.
    chown -R "${SUDO_USER}:$(id -gn "$SUDO_USER")" "$VENV_DIR"
  fi
  if [[ -d "$VENV_DIR" ]]; then
    local python_bin
    python_bin=$(venv_python)
    if [[ -n "$python_bin" ]] && "$python_bin" -m pip --version >/dev/null 2>&1; then
      return
    fi
    # A venv created before python3-venv was installed has no pip and cannot
    # be repaired reliably; rebuild it.
    echo "Existing virtual environment at ${VENV_DIR} is unusable; recreating it." >&2
    rm -rf "$VENV_DIR"
  fi

  as_checkout_owner "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  echo "Created virtual environment at ${VENV_DIR}." >&2
}

venv_python() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s' "${VENV_DIR}/bin/python"
    return
  fi
  printf ''
}

install_dependencies() {
  local python_bin
  python_bin=$(venv_python)

  if [[ -z "$python_bin" ]]; then
    echo "Error: Unable to locate virtualenv python interpreter." >&2
    exit 1
  fi

  as_checkout_owner "$python_bin" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
  # Pin runtime dependencies to the same lock used by production releases and
  # add the test tooling from the dev extra.
  as_checkout_owner "$python_bin" -m pip install --disable-pip-version-check \
    --requirement "${PROJECT_ROOT}/requirements.lock" --editable "${PROJECT_ROOT}[dev]"
}

run_migrations() {
  local python_bin
  python_bin=$(venv_python)
  echo "Applying database migrations…" >&2
  (cd "$PROJECT_ROOT" && as_checkout_owner "$python_bin" manage.py migrate --target-release development)
}

install_development_service() {
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "systemd is not active on this host; skipping service setup." >&2
    return
  fi
  if ! is_root; then
    echo "Installer is not running as root; skipping systemd service setup." >&2
    echo "Run this installer with sudo to auto-create and start the service." >&2
    return
  fi

  local service_name
  service_name=$(read_env_value "SYSTEMD_SERVICE_NAME" "myportal-dev")
  service_name=${service_name:-myportal-dev}
  if [[ "$service_name" != *.service ]]; then
    service_name="${service_name}.service"
  fi
  if [[ "$service_name" == "myportal.service" ]]; then
    # Never overwrite the name used by a production installation.
    service_name="myportal-dev.service"
  fi

  local service_user service_group
  service_user=$(stat -c '%U' "$PROJECT_ROOT" 2>/dev/null || true)
  if [[ -z "$service_user" || "$service_user" == "UNKNOWN" || "$service_user" == "root" ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
      service_user="$SUDO_USER"
    else
      service_user=$(id -un)
    fi
  fi
  service_group=$(id -gn "$service_user" 2>/dev/null || id -gn)

  local port
  port=$(read_env_value "DEV_SERVER_PORT" "8000")
  port=${port:-8000}

  local unit_path="/etc/systemd/system/${service_name}"
  cat >"$unit_path" <<UNIT
[Unit]
Description=MyPortal customer portal (development checkout ${PROJECT_ROOT})
After=network-online.target mariadb.service mysql.service redis.service
Wants=network-online.target

[Service]
# Uvicorn does not implement systemd's sd_notify protocol.
Type=simple
User=${service_user}
Group=${service_group}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${port}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
# read-only (rather than true) keeps checkouts under /home usable.
ProtectHome=read-only
ReadWritePaths=${PROJECT_ROOT}

[Install]
WantedBy=multi-user.target
UNIT

  chmod 644 "$unit_path"
  systemctl daemon-reload
  if systemctl enable "$service_name" >/dev/null 2>&1 && systemctl restart "$service_name"; then
    echo "Systemd service '${service_name}' is enabled and running on port ${port}." >&2
  else
    echo "Warning: Failed to enable/start '${service_name}' automatically." >&2
    echo "Run: sudo systemctl status ${service_name}" >&2
  fi
}

# ---------------------------------------------------------------------------
# PowerShell Core (pwsh) – optional dependency for Exchange Online fallback
# ---------------------------------------------------------------------------

install_pwsh() {
  # Skip if pwsh is already available.
  if command -v pwsh >/dev/null 2>&1; then
    echo "PowerShell Core (pwsh) is already installed." >&2
    return
  fi

  # Only attempt installation on Debian/Ubuntu where apt-get is available.
  if ! command -v apt-get >/dev/null 2>&1 || ! is_root; then
    echo "Warning: skipping optional PowerShell Core installation (needs root and apt-get)." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi

  echo "Installing PowerShell Core (pwsh)…" >&2

  if ! apt-get install -y -qq wget >/dev/null; then
    echo "Warning: Failed to install wget – skipping PowerShell Core installation." >&2
    return
  fi

  # Detect the running distribution.  /etc/os-release is standard on all
  # systemd-based distributions.
  local distro_id="" version_id=""
  if [[ -f /etc/os-release ]]; then
    distro_id=$(. /etc/os-release && printf '%s' "${ID:-}")
    version_id=$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")
  fi

  case "$distro_id" in
    ubuntu|debian) ;;
    *)
      echo "Warning: Unsupported distribution '${distro_id:-unknown}' – skipping PowerShell Core installation." >&2
      echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
      return
      ;;
  esac

  # Register the Microsoft package repository.
  local pkg_url="https://packages.microsoft.com/config/${distro_id}/${version_id}/packages-microsoft-prod.deb"
  local tmp_deb
  tmp_deb=$(mktemp /tmp/packages-microsoft-prod.XXXXXX.deb)
  if ! wget -q -O "$tmp_deb" "$pkg_url"; then
    rm -f "$tmp_deb"
    echo "Warning: Failed to download Microsoft package list for ${distro_id} ${version_id}." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi
  if ! dpkg -i "$tmp_deb" >/dev/null; then
    rm -f "$tmp_deb"
    echo "Warning: Failed to register the Microsoft package repository." >&2
    return
  fi
  rm -f "$tmp_deb"

  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed after adding Microsoft repository." >&2
    return
  fi
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq powershell; then
    echo "Warning: Failed to install powershell package." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi

  if command -v pwsh >/dev/null 2>&1; then
    echo "PowerShell Core installed successfully." >&2
  else
    echo "Warning: PowerShell Core package installed but pwsh not found on PATH." >&2
  fi
}

install_exo_module() {
  local pwsh_bin
  pwsh_bin=$(command -v pwsh 2>/dev/null || true)

  if [[ -z "$pwsh_bin" ]] || ! is_root; then
    echo "Warning: skipping optional ExchangeOnlineManagement module install (needs root and pwsh)." >&2
    return
  fi

  # Check if the module is already installed.
  if "$pwsh_bin" -NoProfile -NonInteractive -Command \
      'if (Get-Module -ListAvailable -Name ExchangeOnlineManagement) { exit 0 } else { exit 1 }' \
      2>/dev/null; then
    echo "ExchangeOnlineManagement PowerShell module is already installed." >&2
    return
  fi

  echo "Installing ExchangeOnlineManagement PowerShell module…" >&2

  # Optional: a PowerShell Gallery outage must not abort the installation.
  "$pwsh_bin" -NoProfile -NonInteractive -Command \
    'Install-Module -Name ExchangeOnlineManagement -Repository PSGallery -Scope AllUsers -Force -AllowClobber' \
    || true

  if "$pwsh_bin" -NoProfile -NonInteractive -Command \
      'if (Get-Module -ListAvailable -Name ExchangeOnlineManagement) { exit 0 } else { exit 1 }' \
      2>/dev/null; then
    echo "ExchangeOnlineManagement module installed successfully." >&2
  else
    echo "Warning: ExchangeOnlineManagement module installation failed; the Exchange Online PowerShell fallback will be unavailable." >&2
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [[ "$ENVIRONMENT" == "production" ]]; then
  check_control_checkout
fi

install_system_packages

if [[ "$ENVIRONMENT" == "production" ]]; then
  ensure_service_account
  prepare_production_directories
fi

ensure_env_file
run_privileged bash "${SCRIPT_DIR}/provision_redis.sh" "$ENV_FILE"
ensure_env_default "DISABLED_FEATURE_PACKS" ""
ensure_env_default "DISABLED_MODULES" ""
ensure_env_default "ENABLE_AUTO_REFRESH" "false"
ensure_env_default "ASSET_TYPE_MODE" "auto"
ensure_env_default "UVICORN_AUTO_UPDATE_ENABLED" "true"
ensure_env_default "UVICORN_AUTO_UPDATE_ATTEMPTS" "2"
ensure_env_default "UVICORN_AUTO_UPDATE_RETRY_DELAY" "5"

# Security: replace placeholder secrets with cryptographically random values.
# Existing non-placeholder values are preserved, so running the installer on an
# already-provisioned host never rotates live keys.
ensure_env_secret "SESSION_SECRET" 48
ensure_env_secret "TOTP_ENCRYPTION_KEY" 48
ensure_env_secret "SMTP2GO_WEBHOOK_SECRET" 32
ensure_env_secret "MCP_TOKEN" 32
# The installer owns the local database account, so do not leave the password
# shipped in .env.example in place on a new deployment.
ensure_env_secret "DB_PASSWORD" 32 placeholder-only
if [[ "$ENVIRONMENT" == "production" ]]; then
  # Immutable releases have no .git directory; the admin "system update"
  # action queries this checkout for the latest revision instead.
  set_env_value MYPORTAL_CONTROL_CHECKOUT "$PROJECT_ROOT"
fi
secure_env_file_permissions

cat <<REMINDER

SECURITY REMINDER:
  - Configuration is stored in ${ENV_FILE} (not readable by other users).
  - If fresh secrets were generated above, store a secure backup. Losing
    TOTP_ENCRYPTION_KEY will make stored TOTP secrets and encrypted
    integration credentials unrecoverable.
  - Rotate SESSION_SECRET and TOTP_ENCRYPTION_KEY at least annually and
    whenever an operator with access to the server leaves.

REMINDER

if is_root; then
  "${SCRIPT_DIR}/provision_mysql.sh" "$ENV_FILE"
else
  sudo "${SCRIPT_DIR}/provision_mysql.sh" "$ENV_FILE"
fi

install_pwsh
install_exo_module
install_sip_client
# Tray binaries and installers are release artifacts built by CI. Installing
# the server must not install Go/.NET/WiX or compile unrelated client software.

if [[ "$ENVIRONMENT" == "production" ]]; then
  # The deployment (scripts/upgrade.sh) also sets up Gitea for the RMM script
  # library with scripts/provision_gitea.sh; nginx serves it at /gitea.
  run_first_deployment
  install_update_cron
  # Install the daily backup timer so scheduled backups start on first boot.
  # The timer unit is idempotent: upgrade.sh refreshes it on every deploy.
  if [[ -f "${PROJECT_ROOT}/deploy/systemd/myportal-backup.service" ]]; then
    install -m 0644 "${PROJECT_ROOT}/deploy/systemd/myportal-backup.service" /etc/systemd/system/myportal-backup.service
    install -m 0644 "${PROJECT_ROOT}/deploy/systemd/myportal-backup.timer" /etc/systemd/system/myportal-backup.timer
    systemctl daemon-reload
    systemctl enable myportal-backup.timer 2>/dev/null || true
    echo "Backup timer installed and enabled (daily by default)."
  fi

  gitea_note=""
  if [[ -f /etc/gitea/admin-credentials ]]; then
    gitea_note="Scripts (Gitea):  http://$(hostname -f 2>/dev/null || hostname)/gitea/
  Sign in as myportal; the password is in /etc/gitea/admin-credentials.

"
  fi
  cat <<MESSAGE
MyPortal production environment is ready.
- Environment file: ${ENV_FILE}
- Control checkout: ${PROJECT_ROOT}
- Serving release:  $(readlink -f "${DEPLOY_ROOT}/current" 2>/dev/null || echo "<unknown>")
- Portal URL:       http://$(hostname -f 2>/dev/null || hostname)/

${gitea_note}Open the portal URL and register the first account; it becomes the super
administrator. Apply future updates from the portal (Administration > System
Updates) or with:
  sudo myportal-upgrade

Before exposing the portal to the internet, terminate TLS in front of nginx,
set PORTAL_URL to the public https:// address and ENVIRONMENT=production in
${ENV_FILE} (production mode issues Secure-only cookies, which browsers only
send over HTTPS), then apply the change with:
  sudo systemctl restart myportal@blue.service myportal@green.service
MESSAGE
else
  ensure_virtualenv
  install_dependencies
  run_migrations
  install_development_service

  cat <<MESSAGE
MyPortal development environment is ready.
- Environment file: ${ENV_FILE}
- Virtualenv:       ${VENV_DIR}

Run the development server manually with:
  ${VENV_DIR}/bin/python -m uvicorn app.main:app --reload
and apply new migrations after pulling changes with:
  ${VENV_DIR}/bin/python manage.py migrate --target-release development
MESSAGE
fi
