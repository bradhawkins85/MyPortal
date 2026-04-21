-- Generic per-user UI preferences (table column visibility, etc.).
-- Idempotent and SQLite-compatible (the runner adapts JSON -> TEXT and ON UPDATE).
CREATE TABLE IF NOT EXISTS user_preferences (
  user_id INT NOT NULL,
  preference_key VARCHAR(190) NOT NULL,
  preference_value JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, preference_key),
  CONSTRAINT fk_user_preferences_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
