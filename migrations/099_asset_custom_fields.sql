-- Asset custom field definitions (global configuration)
CREATE TABLE IF NOT EXISTS asset_custom_field_definitions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  field_type ENUM('text', 'image', 'checkbox', 'url', 'date') NOT NULL,
  display_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY unique_name (name)
);

-- Asset custom field values (per asset)
CREATE TABLE IF NOT EXISTS asset_custom_field_values (
  id INT AUTO_INCREMENT PRIMARY KEY,
  asset_id INT NOT NULL,
  field_definition_id INT NOT NULL,
  value_text TEXT,
  value_date DATE,
  value_boolean BOOLEAN,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  FOREIGN KEY (field_definition_id) REFERENCES asset_custom_field_definitions(id) ON DELETE CASCADE,
  UNIQUE KEY unique_asset_field (asset_id, field_definition_id)
);
