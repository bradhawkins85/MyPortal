#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (Linux).
#
# MyPortal sets these for each run:
#   MESH_SITE     the MeshCentral server address (https://...)
#   MESH_GROUP_ID the device group to install into (the device's company group)
#
# Downloads the self-installing MeshCentral agent scoped to that group and runs
# it, so the device joins its company's group. The node id is resolved by
# MyPortal afterwards (the agent has no command-line that prints it).
set -euo pipefail

if [ -z "${MESH_SITE:-}" ]; then
  echo "The MeshCentral server address is missing." >&2
  exit 1
fi
if [ -z "${MESH_GROUP_ID:-}" ]; then
  echo "The MeshCentral device group is missing." >&2
  exit 1
fi

# MeshCentral's Linux agent type ids.
case "$(uname -m)" in
  x86_64|amd64) ARCH_ID=6 ;;
  arm64|aarch64) ARCH_ID=32 ;;
  *) ARCH_ID=5 ;;
esac

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
INSTALLER="$(mktemp).sh"
trap 'rm -f "$INSTALLER"' EXIT
echo "Installing the MeshCentral agent ($(uname -m), into group $MESH_GROUP_ID)"
# The downloaded artifact is a self-installing, background installer.
curl -fsSL --max-time 180 -o "$INSTALLER" "$MESH_SITE/meshagents?id=${ARCH_ID}&meshid=${MESH_GROUP_ID}&installflags=2"
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
