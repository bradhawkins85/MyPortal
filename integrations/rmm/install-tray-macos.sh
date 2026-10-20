#!/bin/bash
# MyPortal Tray Agent — macOS install script for any RMM.
#
# Downloads the tray pkg from your MyPortal server, installs it with the
# company's enrolment token, checks the service and menu-bar agent are
# running, then waits for the device to enrol.
#
# Use one copy for every company: only the values change. Pass them as
# options, or as environment variables for RMMs that inject script variables
# that way (options win when both are set).
#
#   --portal-url URL     / MYPORTAL_URL      Full MyPortal address, e.g. https://portal.example.com
#   --enrol-token TOKEN  / ENROL_TOKEN       The company's tray install token. Store it as a
#                                            secret/protected variable in your RMM.
#   --auto-update BOOL   / AUTO_UPDATE       true (default) or false.
#   --pkg-url URL        / MYPORTAL_PKG_URL  Optional: download the pkg from somewhere else
#                                            (default <portal-url>/static/tray/myportal-tray.pkg).
#   --wait-seconds N     / WAIT_SECONDS      Seconds to wait for enrolment (default 120, 0 skips).
#   --force              / FORCE=true        Reinstall even if already enrolled with this portal.
#
# Run as root, for example:
#   sudo bash install-tray-macos.sh --portal-url 'https://portal.example.com' --enrol-token 'TOKEN'
#
# Exit codes: 0 installed/enrolled (or already installed), 1 bad parameters,
# 2 download failed, 3 pkg install failed, 4 installed but not enrolled in
# time, 5 not running as root.
set -uo pipefail

PORTAL_URL="${MYPORTAL_URL:-}"
TOKEN="${ENROL_TOKEN:-}"
AUTO_UPDATE="${AUTO_UPDATE:-true}"
PKG_URL="${MYPORTAL_PKG_URL:-}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"
FORCE="${FORCE:-false}"

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --portal-url) PORTAL_URL="${2:-}"; shift 2 ;;
        --enrol-token) TOKEN="${2:-}"; shift 2 ;;
        --auto-update) AUTO_UPDATE="${2:-}"; shift 2 ;;
        --pkg-url) PKG_URL="${2:-}"; shift 2 ;;
        --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

ENV_FILE="/Library/Preferences/io.myportal.tray.env"
STATE_FILE="/Library/Application Support/MyPortal/Tray/tray-state.json"
DAEMON_LABEL="io.myportal.tray.service"
AGENT_LABEL="io.myportal.tray.agent"
LOG_FILE="/Library/Logs/MyPortal/tray/rmm-install.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Read a top-level string from the tray state JSON without python3, which a
# fresh Mac does not have (calling it prompts for the developer tools).
state_value() {
    [ -f "$STATE_FILE" ] || return 0
    plutil -extract "$1" raw -o - "$STATE_FILE" 2>/dev/null \
        || sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$STATE_FILE" 2>/dev/null
}

# ── Validate ─────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    log "ERROR: run this script as root (most RMMs do; otherwise use sudo)."
    exit 5
fi

PORTAL_URL="${PORTAL_URL%/}"
if ! [[ "$PORTAL_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]]; then
    log "ERROR: portal URL must be an https:// address (got '$PORTAL_URL')."
    exit 1
fi
if ! [[ "$TOKEN" =~ ^[A-Za-z0-9_-]{16,128}$ ]]; then
    log "ERROR: enrolment token is missing or has an unexpected format."
    exit 1
fi
case "$(echo "$AUTO_UPDATE" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) AUTO_UPDATE=true ;;
    0|false|no) AUTO_UPDATE=false ;;
    *) log "ERROR: auto-update must be true or false (got '$AUTO_UPDATE')."; exit 1 ;;
esac
if ! [[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
    log "ERROR: wait-seconds must be a whole number (got '$WAIT_SECONDS')."
    exit 1
fi
[ -n "$PKG_URL" ] || PKG_URL="$PORTAL_URL/static/tray/myportal-tray.pkg"

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null && exec > >(tee -a "$LOG_FILE") 2>&1
log "MyPortal Tray RMM install for $PORTAL_URL (macOS $(sw_vers -productVersion 2>/dev/null))"

# ── Skip if already enrolled with this portal ────────────────────────────────
DEVICE_UID="$(state_value device_uid)"
ENROLLED_URL="$(state_value portal_url)"
ENROLLED_URL="${ENROLLED_URL%/}"
if [ "$FORCE" != "true" ] && [ -n "$DEVICE_UID" ] && [ "$ENROLLED_URL" = "$PORTAL_URL" ] \
    && [ -x /Library/MyPortal/Tray/myportal-tray-service ]; then
    if ! launchctl print "system/$DAEMON_LABEL" >/dev/null 2>&1; then
        log "Tray service is installed but not loaded; loading it."
        launchctl enable "system/$DAEMON_LABEL" 2>/dev/null || true
        launchctl bootstrap system "/Library/LaunchDaemons/$DAEMON_LABEL.plist" 2>/dev/null || true
    fi
    launchctl kickstart "system/$DAEMON_LABEL" 2>/dev/null || true
    log "MyPortal Tray is already installed and enrolled (device $DEVICE_UID). Use --force to reinstall."
    exit 0
fi

# ── Download ─────────────────────────────────────────────────────────────────
WORK_DIR="$(mktemp -d /tmp/myportal-tray.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
PKG_PATH="$WORK_DIR/myportal-tray.pkg"

log "Downloading $PKG_URL"
if ! curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 "$PKG_URL" -o "$PKG_PATH"; then
    log "ERROR: download failed from $PKG_URL."
    exit 2
fi
log "Downloaded $(( $(stat -f '%z' "$PKG_PATH") / 1048576 )) MB."

# ── Configure and install ────────────────────────────────────────────────────
# A device moving to another portal must enrol afresh.
if [ -n "$ENROLLED_URL" ] && [ "$ENROLLED_URL" != "$PORTAL_URL" ]; then
    log "Existing install is enrolled with '$ENROLLED_URL'; re-enrolling with $PORTAL_URL."
    rm -f "$STATE_FILE"
fi

# The pkg's postinstall reads this file before starting the service.
( umask 077; printf 'MYPORTAL_URL=%s\nENROL_TOKEN=%s\nAUTO_UPDATE=%s\n' \
    "$PORTAL_URL" "$TOKEN" "$AUTO_UPDATE" > "$ENV_FILE" )
chown root:wheel "$ENV_FILE"
chmod 600 "$ENV_FILE"

log "Installing package (auto-update $AUTO_UPDATE)"
if ! installer -pkg "$PKG_PATH" -target / ; then
    log "ERROR: installer failed. See /var/log/install.log and /Library/Logs/MyPortal/tray/postinstall.log."
    exit 3
fi

# ── Check launchd jobs ───────────────────────────────────────────────────────
if launchctl print "system/$DAEMON_LABEL" >/dev/null 2>&1; then
    log "Tray service is loaded."
else
    log "WARNING: tray service is not loaded. Check System Settings > General > Login Items"
    log "         and make sure MyPortal is allowed in the background."
fi
CONSOLE_UID="$(stat -f '%u' /dev/console 2>/dev/null || echo 0)"
if [ "$CONSOLE_UID" -ge 500 ] 2>/dev/null; then
    if launchctl print "gui/$CONSOLE_UID/$AGENT_LABEL" >/dev/null 2>&1; then
        log "Menu-bar agent is loaded for the logged-in user."
    else
        log "WARNING: menu-bar agent is not loaded for user $CONSOLE_UID; it will start at next login."
    fi
else
    log "No user is logged in; the menu-bar agent will start at next login."
fi

# ── Wait for enrolment ───────────────────────────────────────────────────────
if [ "$WAIT_SECONDS" -eq 0 ]; then
    log "Installed. Skipping the enrolment check."
    exit 0
fi
log "Waiting up to ${WAIT_SECONDS}s for enrolment ..."
DEADLINE=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    DEVICE_UID="$(state_value device_uid)"
    if [ -n "$DEVICE_UID" ]; then
        log "MyPortal Tray installed and enrolled (device $DEVICE_UID)."
        exit 0
    fi
    sleep 3
done

log "WARNING: installed, but the device did not enrol within ${WAIT_SECONDS}s."
log "Check the token is valid and this Mac can reach $PORTAL_URL."
log "Service log: /Library/MyPortal/Tray/logs/service.log"
exit 4
