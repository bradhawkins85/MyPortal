-- Network discovery performed by nominated MyPortal tray agents.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS mac_address VARCHAR(17) NULL;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS network_scanner_enabled TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS network_scan_interval_minutes INT NOT NULL DEFAULT 60;

CREATE TABLE IF NOT EXISTS network_devices (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  scanner_tray_device_id INT NOT NULL,
  ip_address VARCHAR(45) NOT NULL,
  mac_address VARCHAR(17) NULL,
  hostname VARCHAR(255) NULL,
  vendor VARCHAR(255) NULL,
  os_details VARCHAR(500) NULL,
  open_ports TEXT NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  matched_asset_id INT NULL,
  UNIQUE KEY uq_network_device_company_mac (company_id, mac_address),
  KEY idx_network_device_company_ip (company_id, ip_address),
  CONSTRAINT fk_network_device_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_network_device_scanner FOREIGN KEY (scanner_tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_network_device_asset FOREIGN KEY (matched_asset_id) REFERENCES assets(id) ON DELETE SET NULL
);
