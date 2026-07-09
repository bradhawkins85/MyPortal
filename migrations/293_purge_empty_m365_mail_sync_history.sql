-- Remove historical M365 mail sync entries that did not process any messages.
-- Future empty sync runs are skipped in application code.

DELETE FROM m365_mail_sync_history
WHERE COALESCE(processed, 0) = 0
  AND COALESCE(created_count, 0) = 0
  AND COALESCE(attached_count, 0) = 0
  AND COALESCE(ignored_count, 0) = 0
  AND COALESCE(error_count, 0) = 0;
