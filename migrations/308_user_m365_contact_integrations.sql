CREATE TABLE IF NOT EXISTS user_m365_contact_integrations (
    user_id BIGINT NOT NULL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    account_email VARCHAR(320) NULL,
    refresh_token TEXT NOT NULL,
    access_token TEXT NULL,
    token_expires_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_m365_contacts_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
