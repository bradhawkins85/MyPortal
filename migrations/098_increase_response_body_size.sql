-- Increase the size of response_body columns from TEXT (64KB) to MEDIUMTEXT (16MB)
-- to accommodate large API responses from Tactical RMM and other integrations

ALTER TABLE webhook_events
  MODIFY COLUMN response_body MEDIUMTEXT NULL;

ALTER TABLE webhook_event_attempts
  MODIFY COLUMN response_body MEDIUMTEXT NULL;
