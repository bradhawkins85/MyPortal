CREATE TABLE staff (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  first_name VARCHAR(255) NOT NULL,
  last_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) NOT NULL,
  date_onboarded DATE,
  enabled TINYINT(1) DEFAULT 1,
  FOREIGN KEY (company_id) REFERENCES companies(id)
);
