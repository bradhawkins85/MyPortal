-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 583: native project management.
--
-- A project belongs to a company and is billed either time and materials
-- (its tickets' time is invoiced as usual) or as a fixed fee (its tickets'
-- time stays off invoices and the fee is billed by milestone). Project tasks
-- are tickets (project_tickets, at most one project per ticket), so time,
-- assignment, SLAs and scheduling work as they do for any ticket. Phases
-- group the tasks; milestones record the fixed-fee payments and the local
-- invoice each one was billed on.

CREATE TABLE IF NOT EXISTS projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'planning',
    billing_type VARCHAR(16) NOT NULL DEFAULT 'time_materials',
    budget_hours DECIMAL(10,2) NULL,
    budget_amount DECIMAL(12,2) NULL,
    start_date DATE NULL,
    due_date DATE NULL,
    manager_user_id INT NULL,
    created_by_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_projects_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_projects_manager FOREIGN KEY (manager_user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_projects_company ON projects (company_id, status);

CREATE TABLE IF NOT EXISTS project_phases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    position INT NOT NULL DEFAULT 0,
    due_date DATE NULL,
    CONSTRAINT fk_project_phases_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_project_phases_project ON project_phases (project_id, position);

CREATE TABLE IF NOT EXISTS project_tickets (
    ticket_id INT NOT NULL PRIMARY KEY,
    project_id INT NOT NULL,
    phase_id INT NULL,
    estimate_hours DECIMAL(10,2) NULL,
    position INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_project_tickets_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_project_tickets_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_project_tickets_phase FOREIGN KEY (phase_id) REFERENCES project_phases(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_project_tickets_project ON project_tickets (project_id, phase_id);

CREATE TABLE IF NOT EXISTS project_milestones (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    phase_id INT NULL,
    name VARCHAR(255) NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    due_date DATE NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    invoice_id INT NULL,
    invoice_number VARCHAR(64) NULL,
    invoiced_at DATETIME NULL,
    position INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_project_milestones_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_project_milestones_phase FOREIGN KEY (phase_id) REFERENCES project_phases(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_project_milestones_project ON project_milestones (project_id, position);
