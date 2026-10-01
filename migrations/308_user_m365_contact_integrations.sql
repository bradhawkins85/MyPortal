CREATE TABLE IF NOT EXISTS user_m365_contact_integrations (
    -- Keep this type identical to users.id so MySQL can create the foreign key.
    user_id INT NOT NULL PRIMARY KEY,
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
