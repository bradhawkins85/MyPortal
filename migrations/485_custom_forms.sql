-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 485: Replace the unused OpnForm integration with Custom Forms.
--
-- Custom Forms lets administrators build forms from a set of standard input
-- types. When a client submits a form, a support ticket is automatically
-- created with the field values attached as details, and any uploaded files
-- are stored as private ticket attachments.
--
-- The tables:
--   * custom_forms holds the form itself (a stable slug is used for the public
--     submission URL).
--   * custom_form_fields holds the ordered list of fields that make up a form.
--   * custom_form_submissions records each submission and the ticket it raised.
--   * custom_form_access optionally restricts a form to specific users. When a
--     form has no access rows it is visible to every authenticated user.
--
-- The legacy OpnForm tables (forms, form_permissions) are unused and are
-- dropped as part of the replacement.
--
-- Note: `?` placeholders are used in repository code (native SQLite, adapted
-- to `%s` for MySQL). DDL here is plain and adapts cleanly to both engines.

DROP TABLE IF EXISTS form_permissions;
DROP TABLE IF EXISTS forms;

CREATE TABLE IF NOT EXISTS custom_forms (
    id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(190) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description VARCHAR(1024) NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_custom_forms_slug UNIQUE (slug),
    CONSTRAINT fk_custom_forms_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS custom_form_fields (
    id INT AUTO_INCREMENT PRIMARY KEY,
    form_id INT NOT NULL,
    field_type VARCHAR(40) NOT NULL,
    label VARCHAR(255) NOT NULL,
    placeholder VARCHAR(255) NULL,
    help_text VARCHAR(2000) NULL,
    required TINYINT(1) NOT NULL DEFAULT 0,
    options JSON NULL,
    default_value VARCHAR(255) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_custom_form_fields_form FOREIGN KEY (form_id) REFERENCES custom_forms(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_custom_form_fields_form_order ON custom_form_fields (form_id, sort_order);

CREATE TABLE IF NOT EXISTS custom_form_submissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    form_id INT NOT NULL,
    ticket_id INT NULL,
    data LONGTEXT NULL,
    ip_address VARCHAR(64) NULL,
    user_agent VARCHAR(500) NULL,
    submitted_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_custom_form_submissions_form FOREIGN KEY (form_id) REFERENCES custom_forms(id) ON DELETE CASCADE,
    CONSTRAINT fk_custom_form_submissions_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
    CONSTRAINT fk_custom_form_submissions_user FOREIGN KEY (submitted_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_custom_form_submissions_form ON custom_form_submissions (form_id, created_at);

CREATE TABLE IF NOT EXISTS custom_form_access (
    id INT AUTO_INCREMENT PRIMARY KEY,
    form_id INT NOT NULL,
    user_id INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_custom_form_access UNIQUE (form_id, user_id),
    CONSTRAINT fk_custom_form_access_form FOREIGN KEY (form_id) REFERENCES custom_forms(id) ON DELETE CASCADE,
    CONSTRAINT fk_custom_form_access_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;