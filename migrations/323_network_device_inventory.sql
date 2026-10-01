-- Admin-maintained metadata for devices found by subnet scanners.
CREATE TABLE IF NOT EXISTS network_device_types (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  UNIQUE KEY uq_network_device_type_name (name)
);

INSERT IGNORE INTO network_device_types (name)
VALUES ('Router'), ('Printer'), ('Switch'), ('AP');

ALTER TABLE network_devices
  ADD COLUMN IF NOT EXISTS state VARCHAR(20) NOT NULL DEFAULT 'New' AFTER matched_asset_id,
  ADD COLUMN IF NOT EXISTS device_type_id INT NULL AFTER state,
  ADD COLUMN IF NOT EXISTS description TEXT NULL AFTER device_type_id;

UPDATE network_devices SET state = 'Known' WHERE matched_asset_id IS NOT NULL;

ALTER TABLE network_devices
  ADD CONSTRAINT fk_network_device_type
  FOREIGN KEY (device_type_id) REFERENCES network_device_types(id) ON DELETE SET NULL;
