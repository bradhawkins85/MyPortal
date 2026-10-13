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
