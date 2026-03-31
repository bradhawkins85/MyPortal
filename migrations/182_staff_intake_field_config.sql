-- Migration 182: Configurable staff intake fields (defaults + per-company overrides)

CREATE TABLE IF NOT EXISTS staff_field_definitions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    field_key VARCHAR(100) NOT NULL UNIQUE,
    label VARCHAR(255) NOT NULL,
    field_type VARCHAR(20) NOT NULL,
    default_visible TINYINT(1) NOT NULL DEFAULT 1,
    default_required TINYINT(1) NOT NULL DEFAULT 0,
    default_sort_order INT NOT NULL DEFAULT 0,
    validation_metadata JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_staff_field_configs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    field_definition_id INT NOT NULL,
    visible TINYINT(1) NULL,
    required TINYINT(1) NULL,
    sort_order INT NULL,
    validation_metadata JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company_staff_field_config (company_id, field_definition_id),
    CONSTRAINT fk_company_staff_field_configs_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_staff_field_configs_definition
        FOREIGN KEY (field_definition_id) REFERENCES staff_field_definitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_staff_field_options (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    field_definition_id INT NOT NULL,
    option_value VARCHAR(255) NOT NULL,
    option_label VARCHAR(255) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_company_field_option (company_id, field_definition_id, option_value),
    CONSTRAINT fk_company_staff_field_options_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_staff_field_options_definition
        FOREIGN KEY (field_definition_id) REFERENCES staff_field_definitions(id) ON DELETE CASCADE
);

INSERT INTO staff_field_definitions (
    field_key,
    label,
    field_type,
    default_visible,
    default_required,
    default_sort_order,
    validation_metadata
) VALUES
    ('first_name', 'First name', 'text', 1, 1, 10, NULL),
    ('last_name', 'Last name', 'text', 1, 1, 20, NULL),
    ('email', 'Email', 'text', 1, 1, 30, JSON_OBJECT('format', 'email')),
    ('mobile_phone', 'Mobile phone', 'text', 1, 0, 40, NULL),
    ('department', 'Department', 'text', 1, 0, 50, NULL),
    ('date_onboarded', 'Onboard date', 'date', 1, 0, 60, NULL),
    ('enabled', 'Enabled', 'checkbox', 1, 0, 70, NULL)
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    field_type = VALUES(field_type),
    default_visible = VALUES(default_visible),
    default_required = VALUES(default_required),
    default_sort_order = VALUES(default_sort_order),
    validation_metadata = VALUES(validation_metadata);
