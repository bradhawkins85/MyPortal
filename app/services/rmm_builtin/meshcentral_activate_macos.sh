#!/bin/bash
# MyPortal built-in: switch MeshCentral on for a remote session (macOS).
# Install the delivered MeshCentral agent for the device's company group, then
# make sure the launch daemon is running. Runs as root through the RMM agent.
# Then reports the agent's node id (meshagent -nodeid) so MyPortal opens that
# device; MyPortal checks it is in the company's group.
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

# Check launchd's system service, not an unrelated foreground meshagent process.
is_active() {
  launchctl print system/meshagent 2>/dev/null | grep -q 'state = running'
}

# Always re-run the full installer: re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
INSTALLER="$MESH_INSTALLER_PATH"
trap 'rm -f "$INSTALLER" "$INSTALLER.msh"' EXIT
if [ ! -s "$INSTALLER.msh" ]; then
  echo "The MeshCentral agent configuration was not delivered by the RMM." >&2
  exit 1
fi
echo "Installing the MeshCentral agent (macOS, into group $MESH_GROUP_ID)"
# Install the service and copy the company configuration alongside its binary.
chmod +x "$INSTALLER"
INSTALL_EXIT=0
INSTALL_OUTPUT=$("$INSTALLER" -fullinstall --copy-msh=1 2>&1) || INSTALL_EXIT=$?
printf '%s\n' "$INSTALL_OUTPUT"
if [ "$INSTALL_EXIT" -ne 0 ]; then exit "$INSTALL_EXIT"; fi
# MeshAgent can report an installer error but still exit with status zero.
if [[ "$INSTALL_OUTPUT" == *"[ERROR]"* ]]; then
  echo "The MeshCentral service installation failed." >&2
  exit 1
fi
for _ in $(seq 1 20); do
  if is_active; then break; fi
  sleep 3
done
if ! is_active; then
  echo "The MeshCentral agent did not finish installing." >&2
  exit 1
fi

echo "The MeshCentral agent is installed and running."

# The installed agent, as launchd runs it.
SUDO=""
AGENT_BIN=$(launchctl print system/meshagent 2>/dev/null | sed -n 's/^[[:space:]]*program = //p' | head -n 1 || true)
if [ -z "$AGENT_BIN" ]; then AGENT_BIN=/usr/local/mesh_services/meshagent/meshagent; fi

# Report the node id (as Tactical RMM reads it) so MyPortal can open this device
# directly. A fresh agent needs a moment to create its identity; without an id,
# MyPortal falls back to matching the device's host name.
NODE_ID=""
for _ in $(seq 1 10); do
  if [ -x "$AGENT_BIN" ]; then
    CANDIDATE=$($SUDO "$AGENT_BIN" -nodeid 2>/dev/null | tr -d '\r' | awk 'NF { $1 = $1; last = $0 } END { print last }' || true)
    if [[ "$CANDIDATE" =~ ^[A-Za-z0-9@\$/_+=.-]{16,128}$ ]]; then NODE_ID="$CANDIDATE"; break; fi
  fi
  sleep 3
done
if [ -n "$NODE_ID" ]; then
  echo "##myportal[session.node_id]=$NODE_ID"
else
  echo "The node id is not available yet; MyPortal will find the device by its host name."
fi
