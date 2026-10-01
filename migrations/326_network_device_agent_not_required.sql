ALTER TABLE network_devices
  ADD COLUMN IF NOT EXISTS agent_not_required TINYINT(1) NOT NULL DEFAULT 0 AFTER description;
