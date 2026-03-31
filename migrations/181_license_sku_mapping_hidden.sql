ALTER TABLE license_sku_friendly_names
  ADD COLUMN hidden TINYINT(1) NOT NULL DEFAULT 0 AFTER friendly_name;
