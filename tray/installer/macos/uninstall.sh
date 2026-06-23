#!/bin/bash
# MyPortal Tray — macOS uninstaller
# Usage: sudo /Library/MyPortal/Tray/uninstall.sh [--purge]
#
# By default this removes installed binaries and launchd jobs while preserving
# device state, preferences, and logs. Pass --purge to also remove local config,
# enrolment state, and logs.
set -u

PURGE=false
if [[ "${1:-}" == "--purge" ]]; then
    PURGE=true
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '1,8p' "$0"
    exit 0
elif [[ -n "${1:-}" ]]; then
    echo "Unknown argument: $1" >&2
    echo "Usage: sudo $0 [--purge]" >&2
    exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This uninstaller must be run as root. Try: sudo $0 ${1:-}" >&2
    exit 1
fi

BIN_DIR="/Library/MyPortal/Tray"
DAEMON_PLIST="/Library/LaunchDaemons/io.myportal.tray.service.plist"
AGENT_PLIST="/Library/LaunchAgents/io.myportal.tray.agent.plist"
ENV_FILE="/Library/Preferences/io.myportal.tray.env"
STATE_DIR="/Library/Application Support/MyPortal/Tray"
LOG_DIR="/Library/Logs/MyPortal/tray"

# Stop currently running per-user agents first so they do not reconnect while
# the daemon is being removed. Ignore all launchctl errors because labels may
# not be loaded in every user session or on every macOS version.
for uid in $(/usr/bin/stat -f '%u' /dev/console 2>/dev/null; /bin/ps -axo uid= 2>/dev/null | /usr/bin/sort -u); do
    if [[ "$uid" =~ ^[0-9]+$ && "$uid" -ge 500 ]]; then
        /bin/launchctl bootout "gui/$uid" "$AGENT_PLIST" 2>/dev/null || true
        /bin/launchctl asuser "$uid" /bin/launchctl unload -w "$AGENT_PLIST" 2>/dev/null || true
    fi
done

/bin/launchctl bootout system "$DAEMON_PLIST" 2>/dev/null || true
/bin/launchctl unload -w "$DAEMON_PLIST" 2>/dev/null || true

/bin/rm -f "$DAEMON_PLIST" "$AGENT_PLIST"
/bin/rm -rf "$BIN_DIR"

# Remove empty parent directories left by the package payload.
/bin/rmdir /Library/MyPortal 2>/dev/null || true

if [[ "$PURGE" == "true" ]]; then
    /bin/rm -f "$ENV_FILE"
    /bin/rm -rf "$STATE_DIR" "$LOG_DIR"
    /bin/rmdir "/Library/Application Support/MyPortal" 2>/dev/null || true
    /bin/rmdir "/Library/Logs/MyPortal" 2>/dev/null || true
fi

echo "MyPortal Tray uninstalled."
if [[ "$PURGE" != "true" ]]; then
    echo "Configuration, state, and logs were preserved. Re-run with --purge to remove them."
fi
