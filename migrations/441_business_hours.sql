-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 441: Business hours. A global schedule (company_id NULL) and
-- optional per-company overrides decide when automations may run, when SLA
-- timers elapse, and whether a customer is currently open.
CREATE TABLE IF NOT EXISTS business_hours_schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    weekly_hours JSON NOT NULL,
    include_global_closures TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_business_hours_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_hours_company ON business_hours_schedules (company_id);

CREATE TABLE IF NOT EXISTS business_hours_closures (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NULL,
    closure_date DATE NOT NULL,
    name VARCHAR(150) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_business_hours_closures_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_business_hours_closures_company_date ON business_hours_closures (company_id, closure_date);

ALTER TABLE automations ADD COLUMN IF NOT EXISTS business_hours_mode VARCHAR(16) NULL;
ALTER TABLE automations ADD COLUMN IF NOT EXISTS business_hours_source VARCHAR(16) NULL;

CREATE TABLE IF NOT EXISTS automation_deferred_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    automation_id INT NOT NULL,
    event_name VARCHAR(128) NULL,
    company_id INT NULL,
    ticket_id INT NULL,
    context JSON NULL,
    run_after DATETIME NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,
    CONSTRAINT fk_automation_deferred_runs_automation FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_automation_deferred_runs_due ON automation_deferred_runs (status, run_after);
CREATE INDEX IF NOT EXISTS idx_automation_deferred_runs_ticket ON automation_deferred_runs (ticket_id);

ALTER TABLE sla_templates ADD COLUMN IF NOT EXISTS business_hours_only TINYINT(1) NOT NULL DEFAULT 0;
