-- Backup History: jobs configured per company.
-- Each job has a unique random token used as the secret in the
-- public POST /api/backup-status webhook so backup scripts can report
-- their status without authenticating.

CREATE TABLE IF NOT EXISTS backup_jobs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  name VARCHAR(200) NOT NULL,
  description TEXT NULL,
  token VARCHAR(64) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_backup_jobs_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_backup_jobs_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE KEY uq_backup_jobs_token (token),
  INDEX idx_backup_jobs_company (company_id),
  INDEX idx_backup_jobs_active (is_active)
);
