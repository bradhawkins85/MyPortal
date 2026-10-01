CREATE TABLE IF NOT EXISTS approval_configurations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(120) NOT NULL,
    description VARCHAR(500) NULL,
    workflow_type VARCHAR(32) NOT NULL DEFAULT 'change',
    technician_user_id INT NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_approval_configuration_company_name UNIQUE (company_id, workflow_type, name),
    CONSTRAINT fk_approval_configuration_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_approval_configuration_technician FOREIGN KEY (technician_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS approval_configuration_contacts (
    configuration_id INT NOT NULL,
    staff_id INT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (configuration_id, staff_id),
    CONSTRAINT fk_approval_contact_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE CASCADE,
    CONSTRAINT fk_approval_contact_staff FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_approval_workflows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    configuration_id INT NOT NULL,
    configuration_name VARCHAR(120) NOT NULL,
    workflow_type VARCHAR(32) NOT NULL DEFAULT 'change',
    assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    assigned_by_user_id INT NULL,
    CONSTRAINT uq_ticket_approval_workflow UNIQUE (ticket_id, workflow_type),
    CONSTRAINT fk_ticket_approval_workflow_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_ticket_approval_workflow_configuration FOREIGN KEY (configuration_id) REFERENCES approval_configurations(id) ON DELETE RESTRICT,
    CONSTRAINT fk_ticket_approval_workflow_assigned_by FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ticket_approval_decisions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workflow_id INT NOT NULL,
    approver_kind VARCHAR(24) NOT NULL,
    approver_user_id INT NULL,
    approver_staff_id INT NULL,
    approver_name VARCHAR(255) NOT NULL,
    approver_email VARCHAR(255) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    decided_at DATETIME(6) NULL,
    decided_by_user_id INT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    CONSTRAINT uq_ticket_approval_decision_user UNIQUE (workflow_id, approver_kind, approver_user_id, approver_staff_id),
    CONSTRAINT fk_ticket_approval_decision_workflow FOREIGN KEY (workflow_id) REFERENCES ticket_approval_workflows(id) ON DELETE CASCADE,
    CONSTRAINT fk_ticket_approval_decision_user FOREIGN KEY (approver_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_ticket_approval_decision_staff FOREIGN KEY (approver_staff_id) REFERENCES staff(id) ON DELETE SET NULL,
    CONSTRAINT fk_ticket_approval_decision_decider FOREIGN KEY (decided_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
