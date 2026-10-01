-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 450: Backup register for the Assets & Network > Backups page.
-- Documents every backup a company relies on: jobs already status tracked in
-- backup_jobs (linked by backup_job_id) and manual entries that are not.
-- Backup applications are a shared, user-extended list so previously entered
-- apps can be searched and reused.

CREATE TABLE IF NOT EXISTS backup_apps (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  created_by INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_backup_apps_name UNIQUE (name),
  CONSTRAINT fk_backup_apps_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS backup_register (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  backup_job_id INT NULL,
  name VARCHAR(200) NOT NULL,
  backup_app_id INT NULL,
  frequency VARCHAR(120) NULL,
  source TEXT NULL,
  destinations TEXT NULL,
  encryption_keys TEXT NULL,
  notes TEXT NULL,
  created_by INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_backup_register_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_backup_register_job FOREIGN KEY (backup_job_id) REFERENCES backup_jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_backup_register_app FOREIGN KEY (backup_app_id) REFERENCES backup_apps(id) ON DELETE SET NULL,
  CONSTRAINT fk_backup_register_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT uq_backup_register_job UNIQUE (backup_job_id),
  INDEX idx_backup_register_company (company_id)
);
