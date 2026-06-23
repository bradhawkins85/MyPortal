-- Migration 275: internal webhook event metadata.
-- Stores non-delivered scheduler metadata used to resume paused workflow tasks
-- after monitored outbound webhook delivery succeeds.

ALTER TABLE webhook_events
    ADD COLUMN IF NOT EXISTS metadata TEXT NULL;
