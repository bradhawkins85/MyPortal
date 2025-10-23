CREATE TABLE IF NOT EXISTS external_api_settings (
  company_id INT PRIMARY KEY,
  xero_endpoint VARCHAR(255),
  xero_api_key VARCHAR(255),
  syncro_endpoint VARCHAR(255),
  syncro_api_key VARCHAR(255),
  FOREIGN KEY (company_id) REFERENCES companies(id)
);
