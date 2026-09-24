-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Versioned M365 connections allow a replacement to be verified before cutover.

CREATE TABLE IF NOT EXISTS m365_connections (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  version INT NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'managed',
  state VARCHAR(24) NOT NULL DEFAULT 'pending',
  tenant_id VARCHAR(255) NOT NULL,
  client_id VARCHAR(255) NOT NULL,
  client_secret TEXT NOT NULL,
  app_object_id VARCHAR(255) NULL,
  service_principal_object_id VARCHAR(255) NULL,
  client_secret_key_id VARCHAR(255) NULL,
  client_secret_expires_at DATETIME NULL,
  verification_tenant TINYINT(1) NOT NULL DEFAULT 0,
  verification_workload TINYINT(1) NOT NULL DEFAULT 0,
  verification_renewal TINYINT(1) NOT NULL DEFAULT 0,
  verification_error TEXT NULL,
  verified_at DATETIME NULL,
  activated_at DATETIME NULL,
  retired_at DATETIME NULL,
  previous_connection_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_m365_connections_company_version (company_id, version),
  UNIQUE KEY uq_m365_connections_company_client (company_id, tenant_id, client_id),
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (previous_connection_id) REFERENCES m365_connections(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_m365_connections_company_state
  ON m365_connections (company_id, state);

CREATE TABLE IF NOT EXISTS m365_connection_dependencies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  connection_id INT NOT NULL,
  dependency_type VARCHAR(32) NOT NULL,
  dependency_key VARCHAR(255) NOT NULL,
  details TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_m365_connection_dependency
    (connection_id, dependency_type, dependency_key),
  FOREIGN KEY (connection_id) REFERENCES m365_connections(id) ON DELETE CASCADE
);
