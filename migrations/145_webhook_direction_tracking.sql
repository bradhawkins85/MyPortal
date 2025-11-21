-- Add direction and source_url fields to webhook_events for tracking incoming vs outgoing webhooks

ALTER TABLE webhook_events
  ADD COLUMN IF NOT EXISTS direction VARCHAR(20) NOT NULL DEFAULT 'outgoing';

ALTER TABLE webhook_events
  ADD COLUMN IF NOT EXISTS source_url VARCHAR(500) NULL;

-- Add index for filtering by direction
CREATE INDEX IF NOT EXISTS idx_webhook_events_direction
  ON webhook_events (direction, status);
