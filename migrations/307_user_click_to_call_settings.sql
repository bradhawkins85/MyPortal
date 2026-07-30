CREATE TABLE IF NOT EXISTS user_click_to_call_settings (
    user_id BIGINT NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    phone_ip VARCHAR(255) NULL,
    login_username VARCHAR(255) NULL,
    password_encrypted TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_click_to_call_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
