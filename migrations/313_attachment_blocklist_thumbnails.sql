ALTER TABLE ticket_attachment_blocklist
  ADD COLUMN IF NOT EXISTS thumbnail_data MEDIUMBLOB NULL AFTER mime_type,
  ADD COLUMN IF NOT EXISTS thumbnail_mime_type VARCHAR(64) NULL AFTER thumbnail_data;
