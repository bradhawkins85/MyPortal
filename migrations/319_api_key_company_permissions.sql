CREATE TABLE IF NOT EXISTS api_key_company_permissions (
    api_key_id INT NOT NULL,
    company_id INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (api_key_id, company_id),
    CONSTRAINT fk_api_key_company_permissions_key
        FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE,
    CONSTRAINT fk_api_key_company_permissions_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_api_key_company_permissions_company
    ON api_key_company_permissions (company_id);
