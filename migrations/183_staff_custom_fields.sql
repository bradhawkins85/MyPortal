-- Migration 183: staff custom field definitions (global/company scope) and per-staff values

CREATE TABLE IF NOT EXISTS staff_custom_field_definitions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NULL,
    base_definition_id INT NULL,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(255) NULL,
    field_type ENUM('text', 'checkbox', 'date', 'select') NOT NULL DEFAULT 'text',
    display_order INT NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_staff_custom_field_name_per_company (company_id, name),
    KEY idx_staff_custom_field_base (base_definition_id),
    CONSTRAINT fk_staff_custom_field_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_custom_field_base
        FOREIGN KEY (base_definition_id) REFERENCES staff_custom_field_definitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staff_custom_field_options (
    id INT AUTO_INCREMENT PRIMARY KEY,
    field_definition_id INT NOT NULL,
    option_value VARCHAR(255) NOT NULL,
    option_label VARCHAR(255) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_staff_custom_field_option (field_definition_id, option_value),
    CONSTRAINT fk_staff_custom_field_options_definition
        FOREIGN KEY (field_definition_id) REFERENCES staff_custom_field_definitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staff_custom_field_values (
    id INT AUTO_INCREMENT PRIMARY KEY,
    staff_id INT NOT NULL,
    field_definition_id INT NOT NULL,
    value_text TEXT NULL,
    value_date DATE NULL,
    value_boolean TINYINT(1) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_staff_custom_field_value (staff_id, field_definition_id),
    CONSTRAINT fk_staff_custom_field_values_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_custom_field_values_definition
        FOREIGN KEY (field_definition_id) REFERENCES staff_custom_field_definitions(id) ON DELETE CASCADE
);
