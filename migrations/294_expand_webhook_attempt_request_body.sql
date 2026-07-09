-- Expand webhook attempt request logging columns so large webhook payloads do not
-- fail with MySQL error 1406 (Data too long for column 'request_body').
-- SQLite stores these as TEXT dynamically and ignores unsupported MODIFY syntax
-- through the migration runner's compatibility handling.

ALTER TABLE webhook_event_attempts
  MODIFY COLUMN request_body LONGTEXT NULL;

ALTER TABLE webhook_event_attempts
  MODIFY COLUMN request_headers LONGTEXT NULL;

ALTER TABLE webhook_event_attempts
  MODIFY COLUMN response_headers LONGTEXT NULL;
