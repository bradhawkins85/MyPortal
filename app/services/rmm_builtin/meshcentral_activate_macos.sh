#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (macOS).
# Run the delivered self-installing
# MeshCentral agent scoped to the device's company group (MESH_GROUP_ID), then
# make sure the launch daemon is running. Runs as root through the RMM agent.
# The node id is resolved by MyPortal afterwards (the agent has no command-line
# that prints it).
#
# MyPortal sets these for each run:
#   MESH_INSTALLER_PATH the installer delivered by MyPortal through the RMM
#   MESH_GROUP_ID the device group to install into (the device's company group)
set -euo pipefail

if [ -z "${MESH_INSTALLER_PATH:-}" ] || [ ! -f "$MESH_INSTALLER_PATH" ]; then
  echo "The MeshCentral installer was not delivered by the RMM." >&2
  exit 1
fi
if [ -z "${MESH_GROUP_ID:-}" ]; then
  echo "The MeshCentral device group is missing." >&2
  exit 1
fi

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
INSTALLER="$MESH_INSTALLER_PATH"
trap 'rm -f "$INSTALLER"' EXIT
echo "Installing the MeshCentral agent (macOS, into group $MESH_GROUP_ID)"
# The downloaded artifact is a self-installing, background installer.
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