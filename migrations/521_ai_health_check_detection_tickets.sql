-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 521: health check detections can raise a ticket.
--
-- ai_health_check_remediations.create_ticket raises a ticket, linked to the
-- asset, for a technician to investigate when a health check finds the
-- detection (app.services.health_check_remediation), alongside or instead of
-- the linked script.
--
-- ai_health_check_detection_tickets keeps the ticket a detection last raised
-- for an asset, so later health checks that find it again add an update to
-- that ticket while it is open rather than raising another.

ALTER TABLE ai_health_check_remediations ADD COLUMN IF NOT EXISTS create_ticket TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS ai_health_check_detection_tickets (
  detection_key VARCHAR(64) NOT NULL,
  asset_id INT NOT NULL,
  ticket_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (detection_key, asset_id),
  CONSTRAINT fk_ai_health_check_detection_tickets_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
