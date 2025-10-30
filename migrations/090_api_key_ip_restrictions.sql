CREATE TABLE IF NOT EXISTS api_key_ip_restrictions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    api_key_id INT NOT NULL,
    cidr VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_api_key_ip_restrictions (api_key_id, cidr),
    CONSTRAINT fk_api_key_ip_restrictions_key
        FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_api_key_ip_restrictions_cidr
    ON api_key_ip_restrictions (cidr);
