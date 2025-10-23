-- Ensure the user_sessions table can persist the active company selection even on
-- MySQL variants that do not support IF NOT EXISTS clauses for ALTER/CREATE operations.
SET @db_name = DATABASE();

SET @column_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND COLUMN_NAME = 'active_company_id'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD COLUMN active_company_id INT NULL AFTER user_id'
  )
);
PREPARE stmt FROM @column_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @index_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.STATISTICS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND INDEX_NAME = 'idx_user_sessions_active_company'
    ),
    'SELECT 1',
    'CREATE INDEX IF NOT EXISTS idx_user_sessions_active_company ON user_sessions (active_company_id)'
  )
);
PREPARE stmt FROM @index_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @fk_stmt = (
  SELECT IF(
    EXISTS (
      SELECT 1
      FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
      WHERE TABLE_SCHEMA = @db_name
        AND TABLE_NAME = 'user_sessions'
        AND CONSTRAINT_NAME = 'fk_user_sessions_active_company'
    ),
    'SELECT 1',
    'ALTER TABLE user_sessions ADD CONSTRAINT fk_user_sessions_active_company FOREIGN KEY (active_company_id) REFERENCES companies(id)'
  )
);
PREPARE stmt FROM @fk_stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE user_sessions AS s
INNER JOIN users AS u ON u.id = s.user_id
SET s.active_company_id = u.company_id
WHERE s.active_company_id IS NULL;
