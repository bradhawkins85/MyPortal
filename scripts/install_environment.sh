#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_TEMPLATE="${PROJECT_ROOT}/.env.example"
VENV_DIR="${PROJECT_ROOT}/.venv"

usage() {
  cat <<'USAGE'
Usage: install_environment.sh <environment>

Prepare a MyPortal installation for the specified environment. Supported
environments are:
  production   Installs dependencies in a dedicated virtual environment using
               regular (non-editable) mode.
  development  Installs dependencies in editable mode to support local
               iteration alongside production deployments.
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
  *)
    echo "Error: Unsupported environment '${ENVIRONMENT}'." >&2
    usage >&2
    exit 1
    ;;
esac

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

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi

  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    echo "Error: ${ENV_TEMPLATE} template not found." >&2
    exit 1
  fi

  cp "$ENV_TEMPLATE" "$ENV_FILE"
  echo "Created ${ENV_FILE} from template." >&2
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
  local key="$1"
  local byte_length="${2:-48}"

  if [[ ! -f "$ENV_FILE" ]]; then
    return
  fi

  ENV_SECRET_KEY="$key" \
    ENV_SECRET_BYTES="$byte_length" \
    ENV_SECRET_FILE="$ENV_FILE" \
    "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
import secrets
from pathlib import Path

env_path = Path(os.environ["ENV_SECRET_FILE"])
key = os.environ["ENV_SECRET_KEY"]
byte_length = int(os.environ.get("ENV_SECRET_BYTES", "48"))

PLACEHOLDER = {
    "",
    "change-me",
    "changeme",
    "change_me",
    "please-change",
    "replace-me",
    "secret",
    "password",
}


def looks_weak(value: str) -> bool:
    stripped = value.strip().strip('"').strip("'")
    if stripped.lower() in PLACEHOLDER:
        return True
    if len(stripped) < 24:
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
  if [[ -f "$ENV_FILE" ]]; then
    chmod 600 "$ENV_FILE" 2>/dev/null || true
  fi
}

ensure_virtualenv() {
  if [[ -d "$VENV_DIR" ]]; then
    return
  fi

  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  echo "Created virtual environment at ${VENV_DIR}." >&2
}

venv_python() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s' "${VENV_DIR}/bin/python"
    return
  fi
  if [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    printf '%s' "${VENV_DIR}/Scripts/python.exe"
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

  "$python_bin" -m pip install --upgrade pip
  if [[ "$ENVIRONMENT" == "development" ]]; then
    "$python_bin" -m pip install --upgrade -e "$PROJECT_ROOT"
  else
    "$python_bin" -m pip install --upgrade "$PROJECT_ROOT"
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
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found – skipping PowerShell Core installation." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi

  echo "Installing PowerShell Core (pwsh)…" >&2

  # Packages required to register the Microsoft package repository.
  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed – skipping PowerShell Core installation." >&2
    return
  fi
  if ! apt-get install -y -qq apt-transport-https software-properties-common wget; then
    echo "Warning: Failed to install prerequisite packages – skipping PowerShell Core installation." >&2
    return
  fi

  # Detect the running distribution.  /etc/os-release is standard on all
  # systemd-based distributions.
  local version_id=""
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    version_id="${VERSION_ID:-}"
  fi

  if [[ -z "$version_id" ]]; then
    echo "Warning: Unable to determine OS version – skipping PowerShell Core installation." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi

  # Register the Microsoft package repository.
  local pkg_url="https://packages.microsoft.com/config/ubuntu/${version_id}/packages-microsoft-prod.deb"
  local tmp_deb
  tmp_deb=$(mktemp /tmp/packages-microsoft-prod.XXXXXX.deb)
  if ! wget -q -O "$tmp_deb" "$pkg_url"; then
    rm -f "$tmp_deb"
    echo "Warning: Failed to download Microsoft package list for Ubuntu ${version_id}." >&2
    echo "Install PowerShell Core manually: https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell" >&2
    return
  fi
  dpkg -i "$tmp_deb"
  rm -f "$tmp_deb"

  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed after adding Microsoft repository." >&2
    return
  fi
  if ! apt-get install -y -qq powershell; then
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

  if [[ -z "$pwsh_bin" ]]; then
    echo "Warning: pwsh not available – skipping ExchangeOnlineManagement module install." >&2
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

  "$pwsh_bin" -NoProfile -NonInteractive -Command \
    'Install-Module -Name ExchangeOnlineManagement -Repository PSGallery -Scope AllUsers -Force -AllowClobber'

  if "$pwsh_bin" -NoProfile -NonInteractive -Command \
      'if (Get-Module -ListAvailable -Name ExchangeOnlineManagement) { exit 0 } else { exit 1 }' \
      2>/dev/null; then
    echo "ExchangeOnlineManagement module installed successfully." >&2
  else
    echo "Warning: ExchangeOnlineManagement module installation may have failed." >&2
  fi
}

# ---------------------------------------------------------------------------
# .NET SDK + WiX v4 – required for building the Windows MSI tray installer
# ---------------------------------------------------------------------------

install_dotnet() {
  if command -v dotnet >/dev/null 2>&1; then
    echo ".NET SDK is already installed ($(dotnet --version))." >&2
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found – skipping .NET SDK installation." >&2
    echo "Install .NET SDK 8+ manually to enable MSI builds: https://dotnet.microsoft.com/download" >&2
    return
  fi

  echo "Installing .NET SDK 8.0…" >&2

  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed – skipping .NET SDK installation." >&2
    return
  fi

  # Try 8.0 first (LTS); fall back to 9.0 if the distro only ships the newer SDK.
  if apt-get install -y -qq dotnet-sdk-8.0 2>/dev/null; then
    :
  elif apt-get install -y -qq dotnet-sdk-9.0 2>/dev/null; then
    :
  else
    echo "Warning: Could not install .NET SDK via apt-get." >&2
    echo "Install .NET SDK 8+ manually: https://dotnet.microsoft.com/download" >&2
    return
  fi

  if command -v dotnet >/dev/null 2>&1; then
    echo ".NET SDK installed ($(dotnet --version))." >&2
  else
    echo "Warning: .NET SDK package installed but dotnet not found on PATH." >&2
  fi
}

install_wix() {
  # WiX v4 is a .NET global tool installed per-user under ~/.dotnet/tools.
  export PATH="${HOME}/.dotnet/tools:${PATH}"

  if command -v wix >/dev/null 2>&1; then
    echo "WiX v4 is already installed." >&2
    return
  fi

  local dotnet_bin
  dotnet_bin=$(command -v dotnet 2>/dev/null || true)

  if [[ -z "$dotnet_bin" ]]; then
    echo "Warning: dotnet not available – skipping WiX v4 installation." >&2
    return
  fi

  echo "Installing WiX v4 (dotnet global tool)…" >&2

  if ! "$dotnet_bin" tool install --global wix 2>/dev/null; then
    # Already installed at a different version; try updating instead.
    if ! "$dotnet_bin" tool update --global wix 2>/dev/null; then
      echo "Warning: Failed to install WiX v4." >&2
      return
    fi
  fi

  # Re-export so the newly installed binary is on PATH for the rest of this session.
  export PATH="${HOME}/.dotnet/tools:${PATH}"

  if command -v wix >/dev/null 2>&1; then
    echo "WiX v4 installed successfully." >&2
  else
    echo "Warning: WiX v4 installed but wix binary not found on PATH." >&2
    echo "Add \${HOME}/.dotnet/tools to PATH to use it." >&2
  fi
}

# ---------------------------------------------------------------------------
# Go toolchain – required for building the tray app binaries
# ---------------------------------------------------------------------------

GO_MIN_MAJOR=1
GO_MIN_MINOR=22

_go_satisfies_version() {
  local go_bin="$1"
  local version_output
  version_output=$("$go_bin" version 2>/dev/null) || return 1
  local major minor
  if [[ "$version_output" =~ go([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if [[ "$major" -gt "$GO_MIN_MAJOR" ]] || \
       [[ "$major" -eq "$GO_MIN_MAJOR" && "$minor" -ge "$GO_MIN_MINOR" ]]; then
      return 0
    fi
  fi
  return 1
}

_detect_go() {
  local -a candidates=("${GOROOT:-/usr/local/go}/bin/go" "go")
  local candidate resolved
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == /* ]]; then
      [[ -x "$candidate" ]] && resolved="$candidate" || continue
    else
      command -v "$candidate" >/dev/null 2>&1 && resolved=$(command -v "$candidate") || continue
    fi
    if _go_satisfies_version "$resolved"; then
      printf '%s' "$resolved"
      return 0
    fi
  done
  return 1
}

install_go() {
  if _detect_go >/dev/null 2>&1; then
    local go_bin
    go_bin=$(_detect_go)
    echo "Go toolchain found: $("$go_bin" version)." >&2
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found – skipping Go installation." >&2
    echo "Install Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ manually to enable tray app builds: https://go.dev/dl/" >&2
    return
  fi

  echo "Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ not found; installing via apt-get…" >&2
  if ! apt-get update -qq; then
    echo "Warning: apt-get update failed – skipping Go installation." >&2
    return
  fi
  if ! apt-get install -y -qq golang-go; then
    echo "Warning: Failed to install golang-go package." >&2
    return
  fi

  if _detect_go >/dev/null 2>&1; then
    local go_bin
    go_bin=$(_detect_go)
    echo "Go toolchain installed: $("$go_bin" version)." >&2
  else
    echo "Warning: golang-go installed but Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ was not detected." >&2
    echo "Install Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ manually: https://go.dev/dl/" >&2
  fi
}

build_tray_installers() {
  local tray_dir="${PROJECT_ROOT}/tray"
  local static_tray_dir="${PROJECT_ROOT}/app/static/tray"

  if [[ ! -f "${tray_dir}/Makefile" ]]; then
    echo "Tray app Makefile not found; skipping tray build." >&2
    return
  fi

  local go_bin
  if ! go_bin=$(_detect_go 2>/dev/null); then
    echo "Go ${GO_MIN_MAJOR}.${GO_MIN_MINOR}+ not available; skipping tray build." >&2
    return
  fi

  if ! command -v make >/dev/null 2>&1; then
    echo "make not available; skipping tray build." >&2
    return
  fi

  # Ensure WiX is on PATH (installed by install_wix above).
  export PATH="${HOME}/.dotnet/tools:${PATH}"

  if ! command -v wix >/dev/null 2>&1; then
    echo "WiX v4 not available; skipping MSI build." >&2
    return
  fi

  echo "Attempting to build Windows MSI installer…" >&2
  local go_dir
  go_dir=$(dirname "$go_bin")
  if (cd "$tray_dir" && PATH="${go_dir}:${HOME}/.dotnet/tools:${PATH}" make build-msi); then
    mkdir -p "$static_tray_dir"
    if [[ -f "${tray_dir}/dist/windows/myportal-tray.msi" ]]; then
      cp "${tray_dir}/dist/windows/myportal-tray.msi" "${static_tray_dir}/myportal-tray.msi"
      echo "MSI installer built and copied → app/static/tray/" >&2
    else
      echo "Warning: MSI build reported success but myportal-tray.msi was not found at expected path." >&2
    fi
  else
    echo "Warning: MSI build failed." >&2
  fi
}

ensure_env_file
ensure_env_default "ENABLE_AUTO_REFRESH" "false"
ensure_env_default "UVICORN_AUTO_UPDATE_ENABLED" "true"
ensure_env_default "UVICORN_AUTO_UPDATE_ATTEMPTS" "2"
ensure_env_default "UVICORN_AUTO_UPDATE_RETRY_DELAY" "5"

# Security: replace placeholder secrets with cryptographically random values.
# Existing non-placeholder values are preserved, so running the installer on an
# already-provisioned host never rotates live keys.
ensure_env_secret "SESSION_SECRET" 48
ensure_env_secret "TOTP_ENCRYPTION_KEY" 48
ensure_env_secret "SMTP2GO_WEBHOOK_SECRET" 32
ensure_env_secret "PLAUSIBLE_PEPPER" 32
ensure_env_secret "MCP_TOKEN" 32
secure_env_file_permissions

cat <<'REMINDER'

SECURITY REMINDER:
  - Your .env file has been set to mode 0600 (owner-only).
  - If fresh secrets were generated above, store a secure backup. Losing
    TOTP_ENCRYPTION_KEY will make stored TOTP secrets and encrypted
    integration credentials unrecoverable.
  - Rotate SESSION_SECRET and TOTP_ENCRYPTION_KEY at least annually and
    whenever an operator with access to the server leaves.

REMINDER

install_pwsh
install_exo_module
install_dotnet
install_wix
install_go
ensure_virtualenv
install_dependencies
build_tray_installers

cat <<MESSAGE
MyPortal ${ENVIRONMENT} environment is ready.
- Environment file: ${ENV_FILE}
- Virtualenv: ${VENV_DIR}

Remember to configure system services (e.g. systemd) and run database migrations
on startup. The application automatically applies migrations during launch.
MESSAGE
