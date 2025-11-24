-- This migration is idempotent to avoid duplicate column/index warnings on reruns

-- Add merged_into_ticket_id column if it does not exist
SET @merged_col_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND COLUMN_NAME = 'merged_into_ticket_id'
);

SET @sql = IF(
    @merged_col_exists = 0,
    'ALTER TABLE tickets ADD COLUMN merged_into_ticket_id INT NULL AFTER external_reference',
    'SELECT "Column merged_into_ticket_id already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add index for merged_into_ticket_id if it does not exist
SET @merged_idx_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND INDEX_NAME = 'idx_tickets_merged_into'
);

SET @sql = IF(
    @merged_idx_exists = 0,
    'CREATE INDEX idx_tickets_merged_into ON tickets(merged_into_ticket_id)',
    'SELECT "Index idx_tickets_merged_into already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add split_from_ticket_id column if it does not exist
SET @split_col_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND COLUMN_NAME = 'split_from_ticket_id'
);

SET @sql = IF(
    @split_col_exists = 0,
    'ALTER TABLE tickets ADD COLUMN split_from_ticket_id INT NULL AFTER merged_into_ticket_id',
    'SELECT "Column split_from_ticket_id already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add index for split_from_ticket_id if it does not exist
SET @split_idx_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND INDEX_NAME = 'idx_tickets_split_from'
);

SET @sql = IF(
    @split_idx_exists = 0,
    'CREATE INDEX idx_tickets_split_from ON tickets(split_from_ticket_id)',
    'SELECT "Index idx_tickets_split_from already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add foreign key for merged_into_ticket_id if it does not exist
SET @merged_fk_exists = (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND CONSTRAINT_NAME = 'fk_tickets_merged_into'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);

SET @sql = IF(
    @merged_fk_exists = 0,
    'ALTER TABLE tickets ADD CONSTRAINT fk_tickets_merged_into FOREIGN KEY (merged_into_ticket_id) REFERENCES tickets(id) ON DELETE SET NULL',
    'SELECT "Foreign key fk_tickets_merged_into already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add foreign key for split_from_ticket_id if it does not exist
SET @split_fk_exists = (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND CONSTRAINT_NAME = 'fk_tickets_split_from'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);

SET @sql = IF(
    @split_fk_exists = 0,
    'ALTER TABLE tickets ADD CONSTRAINT fk_tickets_split_from FOREIGN KEY (split_from_ticket_id) REFERENCES tickets(id) ON DELETE SET NULL',
    'SELECT "Foreign key fk_tickets_split_from already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
