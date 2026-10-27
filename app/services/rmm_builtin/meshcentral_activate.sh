#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (Linux).
#
# MyPortal sets these for each run:
#   MESH_INSTALLER_PATH the installer delivered by MyPortal through the RMM
#   MESH_GROUP_ID the device group to install into (the device's company group)
#
# Installs the delivered MeshCentral agent as a background service so the device
# joins its company's group. The node id is resolved by
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

# Always re-run the full installer: re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
INSTALLER="$MESH_INSTALLER_PATH"
trap 'rm -f "$INSTALLER" "$INSTALLER.msh"' EXIT
if [ ! -s "$INSTALLER.msh" ]; then
  echo "The MeshCentral agent configuration was not delivered by the RMM." >&2
  exit 1
fi
echo "Installing the MeshCentral agent ($(uname -m), into group $MESH_GROUP_ID)"
# Install the service and copy the company configuration alongside its binary.
chmod +x "$INSTALLER"
INSTALL_EXIT=0
INSTALL_OUTPUT=$($SUDO "$INSTALLER" -fullinstall --copy-msh=1 2>&1) || INSTALL_EXIT=$?
printf '%s\n' "$INSTALL_OUTPUT"
if [ "$INSTALL_EXIT" -ne 0 ]; then exit "$INSTALL_EXIT"; fi
# MeshAgent can report an installer error but still exit with status zero.
if [[ "$INSTALL_OUTPUT" == *"[ERROR]"* ]]; then
  echo "The MeshCentral service installation failed." >&2
  exit 1
fi
# Enable and start the installed service before checking that it is running.
$SUDO systemctl enable meshagent
$SUDO systemctl restart meshagent
for _ in $(seq 1 20); do
  if systemctl is-active --quiet meshagent 2>/dev/null; then break; fi
  sleep 3
done
if ! systemctl is-active --quiet meshagent 2>/dev/null; then
  echo "The MeshCentral agent did not finish installing." >&2
  exit 1
fi

echo "The MeshCentral agent is installed and running."
