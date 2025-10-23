CREATE TABLE IF NOT EXISTS user_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  session_token CHAR(64) NOT NULL UNIQUE,
  csrf_token CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  expires_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  ip_address VARCHAR(45) NULL,
  user_agent VARCHAR(255) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  pending_totp_secret TEXT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS login_rate_limits (
  id INT AUTO_INCREMENT PRIMARY KEY,
  identifier VARCHAR(255) NOT NULL UNIQUE,
  window_start DATETIME NOT NULL,
  attempts INT NOT NULL
);

ALTER TABLE users
  ADD COLUMN is_super_admin TINYINT(1) NOT NULL DEFAULT 0;

UPDATE users SET is_super_admin = 1
WHERE id = (
  SELECT id FROM (
    SELECT id FROM users ORDER BY id ASC LIMIT 1
  ) AS first_user
);
