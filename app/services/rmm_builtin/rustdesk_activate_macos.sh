#!/bin/bash
# MyPortal built-in: switch RustDesk on for a remote session (macOS).
# Detect an existing installation, otherwise install the latest stable GitHub
# release for the Mac's architecture (RUSTDESK_INSTALLER_URL optionally
# overrides the .dmg download URL). Then install the RustDesk launch daemon and
# agent, apply RUSTDESK_CFG, restart the service and set
# MYPORTAL_RUSTDESK_PASSWORD. Runs as root through the RMM agent.
set -euo pipefail

APP=/Applications/RustDesk.app
EXE="$APP/Contents/MacOS/RustDesk"
DAEMON_LABEL=com.carriez.RustDesk_service
AGENT_LABEL=com.carriez.RustDesk_server
DAEMON_PLIST="/Library/LaunchDaemons/$DAEMON_LABEL.plist"
AGENT_PLIST="/Library/LaunchAgents/$AGENT_LABEL.plist"
# The agent stops a run after 300 seconds; finish (or fail) before that.
DEADLINE=$((SECONDS + 280))

WORK="$(mktemp -d)"
MOUNT=""
cleanup() {
  if [ -n "$MOUNT" ]; then
    hdiutil detach "$MOUNT" -force >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
  echo "$1" >&2
  exit 1
}

# Run a RustDesk command with a time limit (macOS has no timeout command) and
# print its output. Fails with the step's name when it errors or overruns.
run_bounded() {
  local limit=$1 step=$2
  shift 2
  local remaining=$((DEADLINE - SECONDS))
  [ "$remaining" -gt 0 ] || fail "RustDesk activation timed out during: $step."
  [ "$limit" -le "$remaining" ] || limit=$remaining
  local out="$WORK/out" status=0 waited=0
  "$@" >"$out" 2>/dev/null &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$limit" ]; then
      kill -9 "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      fail "RustDesk timed out during: $step."
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" || status=$?
  [ "$status" -eq 0 ] || fail "RustDesk failed during: $step (exit code $status)."
  cat "$out"
}

download() {
  curl -fsSL --proto '=https' --connect-timeout 15 --max-time "$1" -A 'MyPortal-RMM' "${@:2}"
}

install_rustdesk() {
  local url="${RUSTDESK_INSTALLER_URL:-}"
  if [ -z "$url" ]; then
    echo "Finding the latest stable RustDesk release on GitHub"
    local arch
    case "$(uname -m)" in
      arm64) arch=aarch64 ;;
      x86_64) arch=x86_64 ;;
      *) fail "Unsupported Mac architecture: $(uname -m)." ;;
    esac
    local release
    release="$(download 30 -H 'Accept: application/vnd.github+json' \
      https://api.github.com/repos/rustdesk/rustdesk/releases/latest)" \
      || fail "Could not read the latest RustDesk release from GitHub."
    local assets
    assets="$(printf '%s' "$release" \
      | grep -Eo "\"browser_download_url\" *: *\"https://[^\"]*/rustdesk-[0-9][^\"/]*-$arch\.dmg\"" \
      | sed -E 's/.*"(https:[^"]+)"$/\1/' || true)"
    [ "$(printf '%s' "$assets" | grep -c . || true)" -eq 1 ] \
      || fail "The latest RustDesk release has no unique macOS $arch installer."
    url="$assets"
    local tag
    tag="$(printf '%s' "$release" | grep -Eo '"tag_name" *: *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true)"
    echo "Installing RustDesk $tag ($arch)"
  else
    echo "Installing RustDesk from the configured installer URL"
  fi

  local dmg="$WORK/rustdesk.dmg"
  download 120 -o "$dmg" "$url" || fail "Could not download the RustDesk installer."
  MOUNT="$WORK/mount"
  mkdir -p "$MOUNT"
  run_bounded 60 "opening the installer" \
    hdiutil attach "$dmg" -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" >/dev/null
  local source
  source="$(find "$MOUNT" -maxdepth 1 -name '*.app' -print -quit)"
  [ -n "$source" ] && [ -x "$source/Contents/MacOS/RustDesk" ] \
    || fail "The RustDesk installer does not contain RustDesk.app."
  rm -rf "$APP"
  run_bounded 90 "installation" ditto "$source" "$APP" >/dev/null
  hdiutil detach "$MOUNT" >/dev/null 2>&1 || hdiutil detach "$MOUNT" -force >/dev/null 2>&1 || true
  MOUNT=""
  xattr -dr com.apple.quarantine "$APP" >/dev/null 2>&1 || true
  [ -x "$EXE" ] || fail "RustDesk did not finish installing."
}

# The same launch daemon (root service) and launch agent (screen server in the
# signed-in session) that RustDesk's own "Install" button creates.
write_plists() {
  local service_command="$EXE --service"
  [ -x "$APP/Contents/MacOS/service" ] && service_command="$APP/Contents/MacOS/service"
  cat >"$DAEMON_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$DAEMON_LABEL</string>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>RunAtLoad</key>
  <true/>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>$service_command</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$APP/Contents/MacOS/</string>
  <key>StandardErrorPath</key>
  <string>/tmp/rustdesk_service.err</string>
  <key>StandardOutPath</key>
  <string>/tmp/rustdesk_service.out</string>
</dict>
</plist>
EOF
  cat >"$AGENT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$AGENT_LABEL</string>
  <key>AssociatedBundleIdentifiers</key>
  <string>com.carriez.rustdesk</string>
  <key>LimitLoadToSessionType</key>
  <array>
    <string>LoginWindow</string>
    <string>Aqua</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>ProgramArguments</key>
  <array>
    <string>$EXE</string>
    <string>--server</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$APP/Contents/MacOS/</string>
</dict>
</plist>
EOF
  chown root:wheel "$DAEMON_PLIST" "$AGENT_PLIST"
  chmod 644 "$DAEMON_PLIST" "$AGENT_PLIST"
}

[ -n "${MYPORTAL_RUSTDESK_PASSWORD:-}" ] || fail "The RustDesk session password is missing."
[ "$(id -u)" -eq 0 ] || fail "The RustDesk activation script must run as root."

echo "Checking whether RustDesk is installed"
if [ -x "$EXE" ]; then
  echo "Using the installed RustDesk client"
else
  install_rustdesk
fi

if [ ! -f "$DAEMON_PLIST" ] || [ ! -f "$AGENT_PLIST" ]; then
  echo "Installing the RustDesk service"
  write_plists
fi
launchctl bootstrap system "$DAEMON_PLIST" >/dev/null 2>&1 || true
CONSOLE_UID="$(stat -f %u /dev/console 2>/dev/null || echo 0)"
if [ "$CONSOLE_UID" != "0" ]; then
  # Start the screen server for the signed-in user; the login window gets it at the next sign-out.
  launchctl bootstrap "gui/$CONSOLE_UID" "$AGENT_PLIST" >/dev/null 2>&1 || true
fi

if [ -n "${RUSTDESK_CFG:-}" ]; then
  echo "Applying the RustDesk server config"
  run_bounded 20 "server configuration" "$EXE" --config "$RUSTDESK_CFG" >/dev/null
fi

echo "Starting the RustDesk service"
launchctl kickstart -k "system/$DAEMON_LABEL" >/dev/null 2>&1 \
  || fail "RustDesk failed during: starting the service."
if [ "$CONSOLE_UID" != "0" ]; then
  launchctl kickstart -k "gui/$CONSOLE_UID/$AGENT_LABEL" >/dev/null 2>&1 || true
fi
sleep 3

echo "Setting a new session password"
run_bounded 20 "session password" "$EXE" --password "$MYPORTAL_RUSTDESK_PASSWORD" >/dev/null

ID=""
ID_DEADLINE=$((SECONDS + 45))
while [ -z "$ID" ] && [ "$SECONDS" -lt "$ID_DEADLINE" ] && [ "$SECONDS" -lt "$DEADLINE" ]; do
  ID="$(run_bounded 20 "reading the device ID" "$EXE" --get-id | tr -d '[:space:]')"
  if [ -n "$ID" ] && ! [[ "$ID" =~ ^[0-9]+$ ]]; then
    fail "RustDesk reported an invalid device ID."
  fi
  [ -n "$ID" ] || sleep 3
done
[ -n "$ID" ] || fail "RustDesk did not report its ID."
echo "macOS asks the signed-in user to allow RustDesk Screen Recording and Accessibility the first time; deploy a PPPC profile through MDM to pre-approve them."
echo "##myportal[session.id]=$ID"
