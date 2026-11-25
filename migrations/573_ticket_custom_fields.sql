-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 573: custom ticket details.
--
-- Admins define extra fields shown under "Additional details" on a ticket.
-- editable = 0 makes a field read-only on the ticket page; automations can
-- still set it. visibility is public (requesters see it on /tickets/N and the
-- ticket list), private (technicians and admins) or admin_only (super admins).
-- Values are stored as text; multi-choice answers are a JSON array.

CREATE TABLE IF NOT EXISTS ticket_custom_field_definitions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  field_key VARCHAR(100) NOT NULL,
  label VARCHAR(255) NOT NULL,
  help_text VARCHAR(500) NULL,
  field_type VARCHAR(32) NOT NULL DEFAULT 'text',
  options TEXT NULL,
  config TEXT NULL,
  visibility VARCHAR(16) NOT NULL DEFAULT 'private',
  editable TINYINT(1) NOT NULL DEFAULT 1,
  show_in_list TINYINT(1) NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ticket_custom_field_key (field_key)
);

CREATE TABLE IF NOT EXISTS ticket_custom_field_values (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ticket_id INT NOT NULL,
  field_id INT NOT NULL,
  value_text TEXT NULL,
  updated_by INT NULL,
  updated_source VARCHAR(32) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (field_id) REFERENCES ticket_custom_field_definitions(id) ON DELETE CASCADE,
  UNIQUE KEY uq_ticket_custom_field_value (ticket_id, field_id)
);
