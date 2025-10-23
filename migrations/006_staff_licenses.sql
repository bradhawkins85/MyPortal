CREATE TABLE IF NOT EXISTS staff_licenses (
  staff_id INT NOT NULL,
  license_id INT NOT NULL,
  PRIMARY KEY (staff_id, license_id),
  FOREIGN KEY (staff_id) REFERENCES staff(id),
  FOREIGN KEY (license_id) REFERENCES licenses(id)
);
