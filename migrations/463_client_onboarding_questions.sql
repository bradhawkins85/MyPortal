-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 463: Reusable custom questions in each onboarding section/site.
-- Answers are snapshots in client_onboardings.submission so later edits or
-- deletion of a definition never alter an already submitted form.

CREATE TABLE IF NOT EXISTS client_onboarding_questions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    label VARCHAR(255) NOT NULL,
    field_type VARCHAR(32) NOT NULL,
    section VARCHAR(32) NOT NULL,
    help_text TEXT NULL,
    required TINYINT(1) NOT NULL DEFAULT 0,
    options_json LONGTEXT NULL,
    display_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
