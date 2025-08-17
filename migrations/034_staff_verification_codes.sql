CREATE TABLE staff_verification_codes (
  staff_id INT PRIMARY KEY,
  code VARCHAR(6) NOT NULL,
  created_at DATETIME NOT NULL,
  FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);
