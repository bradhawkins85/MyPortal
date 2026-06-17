-- Migration 270: Unique Wait For Webhook callback URLs for staff onboarding/offboarding workflows

ALTER TABLE staff_onboarding_external_checkpoints
    ADD COLUMN IF NOT EXISTS webhook_public_id VARCHAR(96) NULL AFTER confirmation_token_hash,
    ADD COLUMN IF NOT EXISTS webhook_post_key_hash CHAR(64) NULL AFTER webhook_public_id;

CREATE INDEX IF NOT EXISTS idx_staff_onboarding_external_checkpoints_webhook
    ON staff_onboarding_external_checkpoints (webhook_public_id, status);
