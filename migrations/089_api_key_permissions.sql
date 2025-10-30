CREATE TABLE IF NOT EXISTS api_key_endpoint_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    api_key_id INT NOT NULL,
    route VARCHAR(255) NOT NULL,
    method VARCHAR(16) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_api_key_endpoint_permissions (api_key_id, route, method),
    CONSTRAINT fk_api_key_endpoint_permissions_key
        FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_api_key_endpoint_permissions_route
    ON api_key_endpoint_permissions (route);
CREATE INDEX IF NOT EXISTS idx_api_key_endpoint_permissions_method
    ON api_key_endpoint_permissions (method);
