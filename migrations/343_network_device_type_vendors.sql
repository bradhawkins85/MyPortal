-- Vendor recommendations and optional automatic classification for discovered devices.
ALTER TABLE network_device_types
  ADD COLUMN IF NOT EXISTS auto_assign TINYINT(1) NOT NULL DEFAULT 0 AFTER name;

CREATE TABLE IF NOT EXISTS network_device_type_vendors (
  device_type_id INT NOT NULL,
  mac_vendor VARCHAR(255) NOT NULL,
  PRIMARY KEY (device_type_id, mac_vendor),
  KEY idx_network_device_type_vendor (mac_vendor),
  CONSTRAINT fk_network_device_type_vendor_type
    FOREIGN KEY (device_type_id) REFERENCES network_device_types(id) ON DELETE CASCADE
);
