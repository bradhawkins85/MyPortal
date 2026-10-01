ALTER TABLE users
  ADD COLUMN passkey_user_handle VARCHAR(128) NULL;

CREATE TABLE IF NOT EXISTS user_passkeys (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  credential_id VARCHAR(512) NOT NULL,
  public_key MEDIUMTEXT NOT NULL,
  sign_count INT NOT NULL DEFAULT 0,
  transports TEXT NULL,
  aaguid VARCHAR(64) NULL,
  credential_device_type VARCHAR(32) NULL,
  credential_backed_up TINYINT(1) NOT NULL DEFAULT 0,
  display_name VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_used_at DATETIME NULL,
  UNIQUE (credential_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS passkey_challenges (
  challenge_id VARCHAR(64) PRIMARY KEY,
  ceremony VARCHAR(32) NOT NULL,
  challenge VARCHAR(255) NOT NULL,
  browser_binding_hash CHAR(64) NULL,
  user_id INT NULL,
  session_id INT NULL,
  expires_at DATETIME NOT NULL,
  consumed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES user_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_passkeys_user_id ON user_passkeys (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_passkey_user_handle ON users (passkey_user_handle);
CREATE INDEX IF NOT EXISTS idx_passkey_challenges_expires_at ON passkey_challenges (expires_at);
CREATE INDEX IF NOT EXISTS idx_passkey_challenges_user_id ON passkey_challenges (user_id);
CREATE INDEX IF NOT EXISTS idx_passkey_challenges_session_id ON passkey_challenges (session_id);
