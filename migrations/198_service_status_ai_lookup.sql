-- Add AI lookup fields to service_status_services
ALTER TABLE service_status_services
    ADD COLUMN IF NOT EXISTS ai_lookup_enabled TINYINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_lookup_url VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS ai_lookup_prompt TEXT NULL,
    ADD COLUMN IF NOT EXISTS ai_lookup_model_override VARCHAR(200) NULL,
    ADD COLUMN IF NOT EXISTS ai_lookup_frequency_operational INT NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS ai_lookup_frequency_degraded INT NOT NULL DEFAULT 15,
    ADD COLUMN IF NOT EXISTS ai_lookup_frequency_partial_outage INT NOT NULL DEFAULT 10,
    ADD COLUMN IF NOT EXISTS ai_lookup_frequency_outage INT NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS ai_lookup_frequency_maintenance INT NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS ai_lookup_last_checked_at DATETIME NULL,
    ADD COLUMN IF NOT EXISTS ai_lookup_last_status VARCHAR(50) NULL,
    ADD COLUMN IF NOT EXISTS ai_lookup_last_message TEXT NULL;
