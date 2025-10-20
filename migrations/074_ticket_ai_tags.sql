ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS ai_tags JSON NULL AFTER ai_summary_updated_at,
    ADD COLUMN IF NOT EXISTS ai_tags_status VARCHAR(32) NULL AFTER ai_tags,
    ADD COLUMN IF NOT EXISTS ai_tags_model VARCHAR(128) NULL AFTER ai_tags_status,
    ADD COLUMN IF NOT EXISTS ai_tags_updated_at DATETIME(6) NULL AFTER ai_tags_model;
