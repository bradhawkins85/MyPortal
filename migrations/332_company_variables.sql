CREATE TABLE IF NOT EXISTS company_variable_definitions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_variable_values (
  company_id INT NOT NULL,
  variable_id INT NOT NULL,
  value TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (company_id, variable_id),
  CONSTRAINT fk_company_variable_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_company_variable_definition FOREIGN KEY (variable_id) REFERENCES company_variable_definitions(id) ON DELETE CASCADE
);
