CREATE TABLE IF NOT EXISTS assets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  type VARCHAR(255),
  serial_number VARCHAR(255),
  status VARCHAR(255),
  FOREIGN KEY (company_id) REFERENCES companies(id)
);
