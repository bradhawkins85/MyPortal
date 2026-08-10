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

The endpoint fetches only that Tactical RMM agent, creates or updates its
MyPortal asset, and links the already-enrolled tray device in the same request.
It does not wait for or run a full client asset import.

## Tactical RMM setup

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
