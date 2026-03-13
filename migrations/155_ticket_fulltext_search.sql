-- Add a FULLTEXT index for ticket search fields used in repository queries.
-- Keeps migration idempotent for reruns.

SET @ticket_fulltext_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tickets'
      AND INDEX_NAME = 'idx_tickets_fulltext_search'
);

SET @sql = IF(
    @ticket_fulltext_exists = 0,
    'ALTER TABLE tickets ADD FULLTEXT INDEX idx_tickets_fulltext_search (subject, description, external_reference)',
    'SELECT "Index idx_tickets_fulltext_search already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
