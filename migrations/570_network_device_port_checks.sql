-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 570: one-off port checks on discovered network devices.
--
-- A technician asks the subnet scanner that found a device to try TCP
-- connections to chosen ports. Only the latest check per device is kept;
-- ports is the requested list in "22,443,8000-8100" form and open_ports the
-- ones that answered. command_id is the tray_command_log row that carried it.

CREATE TABLE IF NOT EXISTS network_device_port_checks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  network_device_id INT NOT NULL,
  scanner_tray_device_id INT NOT NULL,
  command_id INT NULL,
  ip_address VARCHAR(45) NOT NULL,
  ports TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  open_ports TEXT NULL,
  checked_count INT NOT NULL DEFAULT 0,
  error VARCHAR(512) NULL,
  requested_by_user_id INT NULL,
  requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  CONSTRAINT fk_network_device_port_checks_device
    FOREIGN KEY (network_device_id) REFERENCES network_devices(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_network_device_port_checks_device
  ON network_device_port_checks (company_id, network_device_id);
CREATE INDEX IF NOT EXISTS idx_network_device_port_checks_scanner
  ON network_device_port_checks (scanner_tray_device_id);
