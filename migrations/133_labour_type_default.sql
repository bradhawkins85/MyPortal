-- Add is_default column to ticket_labour_types table
-- Only one labour type can be default at a time
-- This migration is idempotent

-- Add is_default column if it doesn't exist
SET @col_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'ticket_labour_types'
    AND COLUMN_NAME = 'is_default'
);

SET @sql = IF(
    @col_exists = 0,
    'ALTER TABLE ticket_labour_types ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 AFTER rate',
    'SELECT "Column is_default already exists in ticket_labour_types" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Add index on is_default for faster lookups
SET @idx_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'ticket_labour_types'
    AND INDEX_NAME = 'idx_ticket_labour_types_default'
);

SET @sql = IF(
    @idx_exists = 0,
    'CREATE INDEX idx_ticket_labour_types_default ON ticket_labour_types(is_default)',
    'SELECT "Index idx_ticket_labour_types_default already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Set first labour type as default if no default exists
-- This ensures existing installations have a valid default
UPDATE ticket_labour_types
SET is_default = 1
WHERE id = (
    SELECT id FROM (
        SELECT id FROM ticket_labour_types
        ORDER BY created_at ASC, id ASC
        LIMIT 1
    ) AS first_labour_type
)
AND NOT EXISTS (
    SELECT 1 FROM ticket_labour_types WHERE is_default = 1
);

