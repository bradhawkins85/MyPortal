ALTER TABLE user_sessions
  ADD COLUMN IF NOT EXISTS active_company_id INT NULL AFTER user_id;

ALTER TABLE user_sessions
  ADD CONSTRAINT fk_user_sessions_active_company
    FOREIGN KEY (active_company_id) REFERENCES companies(id);

CREATE INDEX IF NOT EXISTS idx_user_sessions_active_company
  ON user_sessions (active_company_id);

UPDATE user_sessions AS s
INNER JOIN users AS u ON u.id = s.user_id
SET s.active_company_id = u.company_id
WHERE s.active_company_id IS NULL;
