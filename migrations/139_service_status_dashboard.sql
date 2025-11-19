-- Service status dashboard tables

CREATE TABLE IF NOT EXISTS service_status_services (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(200) NOT NULL,
  description TEXT NULL,
  status VARCHAR(50) NOT NULL DEFAULT 'operational',
  status_message TEXT NULL,
  display_order INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by INT NULL,
  CONSTRAINT fk_service_status_services_updated_by FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_service_status_services_status (status),
  INDEX idx_service_status_services_display_order (display_order)
);

CREATE TABLE IF NOT EXISTS service_status_service_companies (
  service_id INT NOT NULL,
  company_id INT NOT NULL,
  PRIMARY KEY (service_id, company_id),
  CONSTRAINT fk_service_status_companies_service FOREIGN KEY (service_id) REFERENCES service_status_services(id) ON DELETE CASCADE,
  CONSTRAINT fk_service_status_companies_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  INDEX idx_service_status_companies_company (company_id)
);
