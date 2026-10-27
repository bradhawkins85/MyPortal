#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (Linux).
#
# MyPortal sets these for each run:
#   MESH_INSTALLER_PATH the installer delivered by MyPortal through the RMM
#   MESH_GROUP_ID the device group to install into (the device's company group)
#
# Runs the self-installing MeshCentral agent delivered for that group,
# it, so the device joins its company's group. The node id is resolved by
# MyPortal afterwards (the agent has no command-line that prints it).
set -euo pipefail

if [ -z "${MESH_INSTALLER_PATH:-}" ] || [ ! -f "$MESH_INSTALLER_PATH" ]; then
  echo "The MeshCentral installer was not delivered by the RMM." >&2
  exit 1
fi
if [ -z "${MESH_GROUP_ID:-}" ]; then
  echo "The MeshCentral device group is missing." >&2
  exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    echo "Installing the MeshCentral agent needs root or sudo." >&2
    exit 1
  fi
fi

# Always re-run the self-installing agent: it is idempotent, and re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
INSTALLER="$MESH_INSTALLER_PATH"
trap 'rm -f "$INSTALLER"' EXIT
echo "Installing the MeshCentral agent ($(uname -m), into group $MESH_GROUP_ID)"
# The downloaded artifact is a self-installing, background installer.
chmod +x "$INSTALLER"
$SUDO "$INSTALLER"
for _ in $(seq 1 20); do
  if systemctl is-active --quiet meshagent 2>/dev/null; then break; fi
  sleep 3
done
if ! systemctl is-active --quiet meshagent 2>/dev/null; then
  echo "The MeshCentral agent did not finish installing." >&2
  exit 1
fi

# Make sure the service is running so the agent checks in.
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable meshagent >/dev/null 2>&1 || true
  $SUDO systemctl restart meshagent >/dev/null 2>&1 || true
fi
echo "The MeshCentral agent is installed and running."
