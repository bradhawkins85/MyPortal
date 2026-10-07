-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 461: Public client onboarding form.
--
-- client_onboardings holds one row per magic link. Only the SHA-256 hash of
-- the link token is stored; the token itself is shown to the admin once when
-- the link is created. submission keeps the details the client entered so
-- admins can review them after the company is created.
--
-- company_site_profiles adds per-site details (phone, primary contact and
-- business hours) to the company_addresses rows the form creates for each
-- site.
--
-- The "New Client" ticket status is used for the support ticket raised when
-- a client completes the form.

CREATE TABLE IF NOT EXISTS client_onboardings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    token_hash CHAR(64) NOT NULL,
    client_name VARCHAR(255) NULL,
    recipient_email VARCHAR(255) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    expires_at DATETIME NOT NULL,
    created_by_user_id INT NULL,
    company_id INT NULL,
    ticket_id INT NULL,
    submission LONGTEXT NULL,
    error_message VARCHAR(500) NULL,
    submitted_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_client_onboardings_token UNIQUE (token_hash),
    CONSTRAINT fk_client_onboardings_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_client_onboardings_status ON client_onboardings (status, created_at);

CREATE TABLE IF NOT EXISTS company_site_profiles (
    address_id INT NOT NULL PRIMARY KEY,
    company_id INT NOT NULL,
    phone VARCHAR(50) NULL,
    primary_contact_staff_id INT NULL,
    timezone VARCHAR(64) NULL,
    weekly_hours LONGTEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_company_site_profiles_address FOREIGN KEY (address_id) REFERENCES company_addresses(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_site_profiles_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_company_site_profiles_company ON company_site_profiles (company_id);

INSERT INTO ticket_statuses (tech_status, tech_label, public_status)
SELECT seed.tech_status, seed.tech_label, seed.public_status
FROM (SELECT 'new_client' AS tech_status, 'New Client' AS tech_label, 'New Client' AS public_status) AS seed
WHERE NOT EXISTS (SELECT 1 FROM ticket_statuses WHERE tech_status = 'new_client');
