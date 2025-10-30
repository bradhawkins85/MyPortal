SET @db_name = DATABASE();

SET @impersonator_user_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND COLUMN_NAME = 'impersonator_user_id'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD COLUMN impersonator_user_id INT NULL AFTER pending_totp_secret'
  )
);
PREPARE stmt FROM @impersonator_user_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @impersonator_session_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND COLUMN_NAME = 'impersonator_session_id'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD COLUMN impersonator_session_id INT NULL AFTER impersonator_user_id'
  )
);
PREPARE stmt FROM @impersonator_session_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @impersonation_started_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND COLUMN_NAME = 'impersonation_started_at'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD COLUMN impersonation_started_at DATETIME NULL AFTER impersonator_session_id'
  )
);
PREPARE stmt FROM @impersonation_started_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @impersonator_session_index_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.STATISTICS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND INDEX_NAME = 'idx_user_sessions_impersonator_session'
    ),
    'SELECT 1',
    'CREATE INDEX idx_user_sessions_impersonator_session ON user_sessions (impersonator_session_id)'
  )
);
PREPARE stmt FROM @impersonator_session_index_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @impersonator_user_fk_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND CONSTRAINT_NAME = 'fk_user_sessions_impersonator_user'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD CONSTRAINT fk_user_sessions_impersonator_user FOREIGN KEY (impersonator_user_id) REFERENCES users(id) ON DELETE SET NULL'
  )
);
PREPARE stmt FROM @impersonator_user_fk_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @impersonator_session_fk_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND CONSTRAINT_NAME = 'fk_user_sessions_impersonator_session'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD CONSTRAINT fk_user_sessions_impersonator_session FOREIGN KEY (impersonator_session_id) REFERENCES user_sessions(id) ON DELETE SET NULL'
  )
);
PREPARE stmt FROM @impersonator_session_fk_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
