-- Add the FULLTEXT index used when ticket searches inspect reply/comment bodies.
-- Keeps migration idempotent for reruns.

SET @ticket_reply_fulltext_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ticket_replies'
      AND INDEX_NAME = 'idx_ticket_replies_body_fulltext'
);

SET @sql = IF(
    @ticket_reply_fulltext_exists = 0,
    'ALTER TABLE ticket_replies ADD FULLTEXT INDEX idx_ticket_replies_body_fulltext (body)',
    'SELECT "Index idx_ticket_replies_body_fulltext already exists" AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
