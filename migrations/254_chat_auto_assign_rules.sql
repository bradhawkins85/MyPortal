-- Matrix Chat Auto-Assign Rules
-- Stores configurable rules for automatically assigning new chat rooms to technicians.

CREATE TABLE IF NOT EXISTS chat_auto_assign_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  priority INT NOT NULL DEFAULT 0,
  is_default TINYINT(1) NOT NULL DEFAULT 0,
  assigned_tech_user_id INT NULL,
  -- JSON array of condition objects:
  -- [{"type": "company_name"|"contact_name"|"subject"|"time_between"|"day_of_week",
  --   "operator": "contains"|"equals"|"starts_with"|"between"|"in",
  --   "value": "<string>"}]
  -- All conditions in a rule are AND-ed together.
  conditions JSON NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_auto_assign_rules_priority ON chat_auto_assign_rules (priority);
CREATE INDEX IF NOT EXISTS idx_chat_auto_assign_rules_is_active ON chat_auto_assign_rules (is_active);
