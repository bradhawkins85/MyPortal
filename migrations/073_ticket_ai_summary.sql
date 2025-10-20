ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS ai_summary TEXT NULL AFTER description,
    ADD COLUMN IF NOT EXISTS ai_summary_status VARCHAR(32) NULL AFTER ai_summary,
    ADD COLUMN IF NOT EXISTS ai_summary_model VARCHAR(128) NULL AFTER ai_summary_status,
    ADD COLUMN IF NOT EXISTS ai_resolution_state VARCHAR(32) NULL AFTER ai_summary_model,
    ADD COLUMN IF NOT EXISTS ai_summary_updated_at DATETIME(6) NULL AFTER ai_resolution_state;
