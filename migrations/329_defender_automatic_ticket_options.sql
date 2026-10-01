-- Company-level Defender alert ticket policies and de-duplication state.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_auto_ticket_antivirus_off TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_auto_ticket_realtime_off TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_auto_ticket_tamper_off TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS defender_auto_ticket_threat_detected TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS defender_alert_tickets (
  id INT PRIMARY KEY AUTO_INCREMENT,
  company_id INT NOT NULL,
  tray_device_id INT NOT NULL,
  alert_type VARCHAR(32) NOT NULL,
  ticket_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_defender_alert_device_type (tray_device_id, alert_type),
  CONSTRAINT fk_defender_alert_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_alert_device FOREIGN KEY (tray_device_id) REFERENCES tray_devices(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_alert_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);
