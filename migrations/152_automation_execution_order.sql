ALTER TABLE automations ADD COLUMN execution_order INT NOT NULL DEFAULT 0 AFTER description;

CREATE INDEX idx_automations_execution_order ON automations(execution_order);
