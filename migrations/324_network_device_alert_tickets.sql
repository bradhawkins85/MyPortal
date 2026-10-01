-- Optional per-company tickets for devices first seen after a subnet baseline scan.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS network_device_ticket_alerts_enabled TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS network_scan_subnets (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  scanner_tray_device_id INT NOT NULL,
  subnet VARCHAR(50) NOT NULL,
  first_scanned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_network_scan_subnet (company_id, subnet),
  CONSTRAINT fk_network_scan_subnet_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_network_scan_subnet_scanner FOREIGN KEY (scanner_tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE
);
