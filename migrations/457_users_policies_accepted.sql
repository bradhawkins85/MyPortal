-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 457: Record policy acceptance version and timestamp on user
-- registration so the system can track which version of the legal policies
-- each user agreed to and when.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS policies_accepted_version VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS policies_accepted_at DATETIME NULL;
