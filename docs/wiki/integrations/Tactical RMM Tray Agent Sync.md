# Tactical RMM immediate Tray Agent sync

The normal Tactical RMM asset import eventually links an enrolled tray device
through the `TrayAgentID` custom field. To make tray menu scripts available
immediately after installation, MyPortal also provides a targeted push endpoint:

```text
POST /api/tray/trmm-sync
X-API-Key: <MyPortal API key>
Content-Type: application/json

{"agent_id":"<TRMM agent ID>","tray_agent_id":"<MyPortal device UID>"}
```

The endpoint first links an existing asset with that Tactical RMM agent ID. If
the asset has not been imported yet, it fetches only that agent, creates the
MyPortal asset, and links the already-enrolled tray device in the same request.
It does not wait for or run a full client asset import.

## Tactical RMM setup — Windows

1. Ensure the company has a Tactical RMM client mapping and the Tactical RMM
   integration is enabled in MyPortal.
2. Create a MyPortal API key. For least privilege, restrict it to `POST` on
   `/api/tray/trmm-sync` and optionally restrict it to the TRMM server IP.
3. Add `integrations/tacticalrmm/sync-tray-agent.ps1` to Tactical RMM.
4. Configure `PortalURL` and `APIKey` as protected script variables. Configure
   `AgentID` with Tactical RMM's runtime variable for the current agent ID.
5. Run the script immediately after the tray installation script, or run it on
   demand for a device that is still waiting to link.

The script waits up to 90 seconds for the tray service to enrol and write
`%ProgramData%\MyPortal\tray\tray-state.json`, reads `device_uid`, and pushes
both identifiers to MyPortal. A successful run prints the linked asset ID.

## Tactical RMM setup — macOS

Use `integrations/tacticalrmm/install-tray-macos.sh`. This single script both
installs the tray agent **and** optionally performs the TRMM sync in one step:

1. Add `integrations/tacticalrmm/install-tray-macos.sh` to Tactical RMM as a
   **bash** script running as **root**.
2. Configure the following Script Variables:

   | Variable | Required | Description |
   |---|---|---|
   | `MYPORTAL_URL` | ✅ | Full URL of the MyPortal server |
   | `ENROL_TOKEN` | ✅ | Per-company install token (mark as **protected**) |
   | `AUTO_UPDATE` | — | `true` (default) or `false` |
   | `PORTAL_API_KEY` | — | MyPortal API key — enables immediate TRMM sync (mark as **protected**) |
   | `TRMM_AGENT_ID` | — | Set to `{{agent.agent_id}}` — required when `PORTAL_API_KEY` is set |
   | `WAIT_SECONDS` | — | Seconds to wait for enrolment before syncing (default `90`) |

3. When `PORTAL_API_KEY` and `TRMM_AGENT_ID` are provided, the script waits for
   the tray service to enrol and write
   `/Library/Application Support/MyPortal/Tray/tray-state.json`, then pushes
   both identifiers to MyPortal in a single `/api/tray/trmm-sync` call.
   A successful run prints the linked asset ID.
4. Without those optional variables the script performs a silent install only;
   the normal Tactical RMM asset import will link the device on its next run.
