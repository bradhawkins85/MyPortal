# Tray device assets

MyPortal provisions an asset during tray enrollment for devices assigned to a
company. This includes devices installed through a Deploy URL. No tray installer
or agent upgrade is required.

On startup, MyPortal checks active tray devices and repairs missing asset links.
An authenticated tray request or WebSocket connection also retries provisioning
when the device has no asset. Company-less and revoked devices are skipped.

MyPortal keeps a valid existing tray asset link. Otherwise it reuses the asset
of an already linked, active RMM agent, then looks for an unambiguous serial or
hostname match within the same company. Archived assets and placeholder serials
are excluded from automatic matching. If no match exists, it creates an asset
with the hostname, serial, operating system and console user supplied by the tray
device. A unique tray identity prevents duplicate creation on retries.

The tray device and its active, same-company MyPortal RMM agents use the same
asset. This enables asset-based script targeting, inventory and other RMM
features when the RMM agent is installed. Creating an asset does not install an
RMM agent or invent a Tactical RMM agent identifier. Existing MyPortal RMM
deployment and Tactical RMM synchronization continue to supply those capabilities.

Apply migration `485_tray_device_assets.sql` during the normal server deployment
before starting the updated application. The startup check then backfills
existing devices. Individual failures are logged and retried on later startup
or, for unlinked devices, the next authenticated tray request.

## New-device setup tickets

After a new tray agent enrols, MyPortal creates an open, normal-priority setup
ticket under its company and links the device's canonical asset. Standard ticket
creation automations apply. No tray agent upgrade is required.

The option **Create setup tickets for new devices** in the company's **Tray
settings** is enabled by default. It can also be managed through the company API
using `tray_setup_tickets_enabled`. Disabling it skips tickets for future
enrolments. Re-enabling it does not create tickets for devices already skipped.

Apply migration `490_tray_setup_tickets.sql` before starting the updated server.
It excludes existing devices from ticket creation. Re-enrolment, heartbeat and
WebSocket reconnects do not create additional tickets, even after the setup
ticket is closed. Temporary failures are logged and retried on subsequent
enrolment, heartbeat or WebSocket check-ins; a partially created ticket is reused.
Company-less and revoked devices do not create tickets.

## Enrolment timeouts and recovery

Enrolment and authenticated check-ins wait at most one second for optional
asset/setup-ticket provisioning. Slow provisioning continues in the background,
shared by check-ins on the same worker. Each attempt has a two-minute limit;
failures are logged and retried by later check-ins. A provisioning failure does
not withhold enrolment credentials or reject an otherwise authenticated device.
An enrolment response can therefore initially have no asset ID; the device and
its eventual asset remain linked through the existing tray identity.

The tray service retries failed initial enrolment with backoff before starting
its authenticated workers. It persists its pending identity separately in
`tray-enrolment-pending.json`, next to `tray-state.json`, so retries and service
restarts reuse the same device UID. Only successful enrolment writes the normal
state file used by the UI and installation scripts.

For a v0.9.7 Mac already stuck after an enrolment timeout, deploy the server fix,
then restart the service to trigger another enrolment attempt:

```bash
sudo launchctl kickstart -k system/io.myportal.tray.service
```

The server change benefits existing clients. Automatic initial-enrolment retry
and pending-identity persistence require a rebuilt tray package. The original
incomplete device record may remain after retrying an older client, which did
not persist its UID before enrolment completed. Confirm the working device
checks in before removing an obsolete record. Persistent asset failures require
checking the portal logs and database migrations, including
`485_tray_device_assets.sql`; enrolment recovery does not repair a missing schema.
