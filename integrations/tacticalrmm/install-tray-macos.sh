#!/bin/bash
# MyPortal Tray — Tactical RMM macOS silent install script
#
# Add this script to Tactical RMM (shell type: bash, run as: root).
# Configure the required variables as protected Script Variables in the TRMM
# script editor; TRMM injects them as environment variables at runtime.
#
# Required Script Variables:
#   MYPORTAL_URL   Full URL of the MyPortal server (e.g. https://portal.example.com)
#   ENROL_TOKEN    Per-company installation token from the MyPortal admin UI
#
# Optional Script Variables:
#   AUTO_UPDATE    true (default) or false — enable in-app auto-update
#   WAIT_SECONDS   Seconds to wait for initial tray service enrolment (default: 90)
#   PORTAL_API_KEY MyPortal API key — when set the script calls /api/tray/trmm-sync
#                  to immediately link this device to the TRMM asset in MyPortal.
#                  Requires POST permission on /api/tray/trmm-sync.
#   TRMM_AGENT_ID  Tactical RMM agent ID — required when PORTAL_API_KEY is set.
#                  Use Tactical RMM's {{agent.agent_id}} runtime variable.
#
set -euo pipefail

# ── Validate required variables ───────────────────────────────────────────────
if [[ -z "${MYPORTAL_URL:-}" ]]; then
    echo "ERROR: MYPORTAL_URL is not set." >&2
    exit 1
fi
if [[ -z "${ENROL_TOKEN:-}" ]]; then
    echo "ERROR: ENROL_TOKEN is not set." >&2
    exit 1
fi

AUTO_UPDATE="${AUTO_UPDATE:-true}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"

PKG_URL="${MYPORTAL_URL%/}/static/tray/myportal-tray.pkg"
PKG_PATH="/tmp/myportal-tray-install.pkg"
ENV_FILE="/Library/Preferences/io.myportal.tray.env"
STATE_FILE="/Library/Application Support/MyPortal/Tray/tray-state.json"

# ── Download ───────────────────────────────────────────────────────────────────
echo "Downloading MyPortal Tray from ${PKG_URL} ..."
if ! curl -fsSL --retry 3 --retry-delay 5 "${PKG_URL}" -o "${PKG_PATH}"; then
    echo "ERROR: Failed to download installer from ${PKG_URL}." >&2
    exit 1
fi

# ── Write configuration ────────────────────────────────────────────────────────
echo "Writing configuration to ${ENV_FILE} ..."
cat > "${ENV_FILE}" <<EOF
MYPORTAL_URL=${MYPORTAL_URL}
ENROL_TOKEN=${ENROL_TOKEN}
AUTO_UPDATE=${AUTO_UPDATE}
EOF
chmod 600 "${ENV_FILE}"

# ── Install package ────────────────────────────────────────────────────────────
echo "Installing MyPortal Tray ..."
if ! installer -pkg "${PKG_PATH}" -target / > /tmp/myportal-tray-install.log 2>&1; then
    echo "ERROR: installer exited with a non-zero status. Log:" >&2
    cat /tmp/myportal-tray-install.log >&2
    exit 1
fi

rm -f "${PKG_PATH}"
echo "MyPortal Tray installed successfully."

# ── Optional: wait for enrolment and sync with MyPortal ───────────────────────
if [[ -n "${PORTAL_API_KEY:-}" ]]; then
    if [[ -z "${TRMM_AGENT_ID:-}" ]]; then
        echo "WARNING: PORTAL_API_KEY is set but TRMM_AGENT_ID is empty — skipping TRMM sync." >&2
    else
        echo "Waiting up to ${WAIT_SECONDS}s for tray service enrolment ..."
        DEADLINE=$(( $(date +%s) + WAIT_SECONDS ))
        TRAY_AGENT_ID=""
        while [[ $(date +%s) -lt ${DEADLINE} ]]; do
            if [[ -f "${STATE_FILE}" ]]; then
                TRAY_AGENT_ID=$(python3 -c "
import json, sys
try:
    d = json.load(open('${STATE_FILE}'))
    uid = d.get('device_uid','')
    if uid: print(uid)
except Exception:
    pass
" 2>/dev/null || true)
                [[ -n "${TRAY_AGENT_ID}" ]] && break
            fi
            sleep 2
        done

        if [[ -z "${TRAY_AGENT_ID}" ]]; then
            echo "WARNING: Tray service did not enrol within ${WAIT_SECONDS}s — skipping TRMM sync." >&2
        else
            echo "Syncing TRMM agent ${TRMM_AGENT_ID} with tray device ${TRAY_AGENT_ID} ..."
            HTTP_STATUS=$(curl -fsSL -o /tmp/myportal-trmm-sync.json -w "%{http_code}" \
                -X POST \
                -H "Content-Type: application/json" \
                -H "X-API-Key: ${PORTAL_API_KEY}" \
                -d "{\"agent_id\":\"${TRMM_AGENT_ID}\",\"tray_agent_id\":\"${TRAY_AGENT_ID}\"}" \
                "${MYPORTAL_URL%/}/api/tray/trmm-sync" 2>/tmp/myportal-trmm-sync.err || echo "000")
            if [[ "${HTTP_STATUS}" == "200" ]]; then
                ASSET_ID=$(python3 -c "
import json
try:
    print(json.load(open('/tmp/myportal-trmm-sync.json')).get('asset_id',''))
except Exception:
    pass
" 2>/dev/null || true)
                echo "Linked TRMM agent ${TRMM_AGENT_ID} to MyPortal asset ${ASSET_ID} and tray device ${TRAY_AGENT_ID}."
            else
                BODY=$(cat /tmp/myportal-trmm-sync.json 2>/dev/null || true)
                echo "WARNING: TRMM sync returned HTTP ${HTTP_STATUS}: ${BODY}" >&2
            fi
            rm -f /tmp/myportal-trmm-sync.json /tmp/myportal-trmm-sync.err
        fi
    fi
fi
