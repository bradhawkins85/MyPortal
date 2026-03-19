CREATE TABLE IF NOT EXISTS group_licenses (
  group_id INT NOT NULL,
  license_id INT NOT NULL,
  PRIMARY KEY (group_id, license_id),
  FOREIGN KEY (group_id) REFERENCES office_groups(id) ON DELETE CASCADE,
  FOREIGN KEY (license_id) REFERENCES licenses(id) ON DELETE CASCADE
);
