-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS process_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    description TEXT NULL,
    current_version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_process_template_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_process_template_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS process_template_steps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    template_id INT NOT NULL,
    version_number INT NOT NULL,
    step_order INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    instructions TEXT NULL,
    CONSTRAINT fk_process_step_template FOREIGN KEY (template_id) REFERENCES process_templates(id) ON DELETE CASCADE,
    CONSTRAINT uq_process_template_step UNIQUE (template_id, version_number, step_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS process_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    template_id INT NOT NULL,
    template_version INT NOT NULL,
    template_name VARCHAR(191) NOT NULL,
    asset_id INT NULL,
    ticket_id INT NULL,
    assignee_id INT NULL,
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    due_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    started_by INT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_by INT NULL,
    completed_at DATETIME NULL,
    CONSTRAINT fk_process_run_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_process_run_template FOREIGN KEY (template_id) REFERENCES process_templates(id),
    CONSTRAINT fk_process_run_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL,
    CONSTRAINT fk_process_run_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
    CONSTRAINT fk_process_run_assignee FOREIGN KEY (assignee_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_process_run_starter FOREIGN KEY (started_by) REFERENCES users(id),
    CONSTRAINT fk_process_run_completer FOREIGN KEY (completed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS process_run_steps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    step_order INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    instructions TEXT NULL,
    notes TEXT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    completed_by INT NULL,
    completed_at DATETIME NULL,
    CONSTRAINT fk_process_run_step_run FOREIGN KEY (run_id) REFERENCES process_runs(id) ON DELETE CASCADE,
    CONSTRAINT fk_process_run_step_completer FOREIGN KEY (completed_by) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uq_process_run_step UNIQUE (run_id, step_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_process_template_company ON process_templates (company_id, is_active, name);
CREATE INDEX idx_process_run_company ON process_runs (company_id, status, due_at);
CREATE INDEX idx_process_run_asset ON process_runs (company_id, asset_id);
CREATE INDEX idx_process_run_ticket ON process_runs (company_id, ticket_id);
