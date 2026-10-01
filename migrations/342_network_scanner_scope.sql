-- Optional safety boundaries for portable subnet scanners.
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS network_scan_wan_cidrs TEXT NULL AFTER network_scan_interval_minutes;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS network_scan_local_cidrs TEXT NULL AFTER network_scan_wan_cidrs;
