ALTER TABLE webhook_event_attempts
  ADD COLUMN IF NOT EXISTS request_headers TEXT NULL;

ALTER TABLE webhook_event_attempts
  ADD COLUMN IF NOT EXISTS request_body TEXT NULL;

ALTER TABLE webhook_event_attempts
  ADD COLUMN IF NOT EXISTS response_headers TEXT NULL;
