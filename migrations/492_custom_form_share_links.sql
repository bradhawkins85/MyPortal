-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS custom_form_share_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    form_id INT NOT NULL,
    company_id INT NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at DATETIME NULL,
    revoked_at DATETIME NULL,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_custom_form_share_token UNIQUE (token_hash),
    CONSTRAINT fk_custom_form_share_form FOREIGN KEY (form_id) REFERENCES custom_forms(id) ON DELETE CASCADE,
    CONSTRAINT fk_custom_form_share_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_custom_form_share_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX idx_custom_form_share_form ON custom_form_share_links (form_id);
