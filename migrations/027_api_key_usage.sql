CREATE TABLE api_key_usage (
  api_key_id INT NOT NULL,
  ip_address VARCHAR(45) NOT NULL,
  usage_count INT NOT NULL DEFAULT 1,
  last_used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (api_key_id, ip_address),
  FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);
