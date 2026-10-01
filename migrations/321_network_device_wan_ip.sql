-- Record the public network location from which each discovery was uploaded.
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS wan_ip VARCHAR(45) NULL AFTER scanner_tray_device_id;
CREATE INDEX IF NOT EXISTS idx_network_device_company_wan ON network_devices (company_id, wan_ip);
