#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (macOS).
# Detect an existing installation, otherwise download and run the self-installing
# MeshCentral agent scoped to the device's company group (MESH_GROUP_ID), then
# make sure the launch daemon is running. Runs as root through the RMM agent.
# The node id is resolved by MyPortal afterwards (the agent has no command-line
# that prints it).
#
# MyPortal sets these for each run:
#   MESH_SITE     the MeshCentral server address (https://...)
#   MESH_GROUP_ID the device group to install into (the device's company group)
set -euo pipefail

if [ -z "${MESH_SITE:-}" ]; then
  echo "The MeshCentral server address is missing." >&2
  exit 1
fi
if [ -z "${MESH_GROUP_ID:-}" ]; then
  echo "The MeshCentral device group is missing." >&2
  exit 1
fi

# MeshCentral's macOS agent type id (universal: Apple Silicon and Intel).
ARCH_ID=10005
# The launch daemon the installer registers.
PLIST="/Library/LaunchDaemons/meshagent.plist"
LABEL="meshagent"

# The agent may already be installed and in the right group.
is_active() {
  pgrep -x meshagent >/dev/null 2>&1 && return 0
  launchctl list 2>/dev/null | grep -q "$LABEL" && return 0
  return 1
}

# Always re-run the self-installing agent: it is idempotent, and re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
INSTALLER="$(mktemp).sh"
trap 'rm -f "$INSTALLER"' EXIT
echo "Installing the MeshCentral agent (macOS, into group $MESH_GROUP_ID)"
# The downloaded artifact is a self-installing, background installer.
curl -fsSL --max-time 180 -o "$INSTALLER" "$MESH_SITE/meshagents?id=${ARCH_ID}&meshid=${MESH_GROUP_ID}&installflags=2"
chmod +x "$INSTALLER"
"$INSTALLER"
for _ in $(seq 1 20); do
  if is_active; then break; fi
  sleep 3
done
if ! is_active; then
  echo "The MeshCentral agent did not finish installing." >&2
  exit 1
fi

# Make sure the service is running so the agent checks in.
if ! pgrep -x meshagent >/dev/null 2>&1; then
  if [ -f "$PLIST" ]; then
    launchctl load -w "$PLIST" >/dev/null 2>&1 || true
    launchctl start "$LABEL" >/dev/null 2>&1 || true
  fi
fi
echo "The MeshCentral agent is installed and running."