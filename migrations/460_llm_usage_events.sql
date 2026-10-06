-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 460: Add llm_usage_events for the Administration > LLM Usage page.
--
-- One row per LLM request MyPortal makes (Ollama, OpenAI-compatible and
-- llama.cpp providers). Records which MyPortal function made the request and
-- the input/output token counts reported by the provider; prompts and
-- responses are not stored. occurred_at is UTC.

CREATE TABLE IF NOT EXISTS llm_usage_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    feature VARCHAR(100) NOT NULL,
    provider VARCHAR(32) NULL,
    model VARCHAR(255) NULL,
    status VARCHAR(16) NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    tokens_estimated TINYINT(1) NOT NULL DEFAULT 0,
    duration_ms INT NULL,
    webhook_event_id INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_llm_usage_events_occurred_at ON llm_usage_events (occurred_at);
CREATE INDEX idx_llm_usage_events_feature_occurred ON llm_usage_events (feature, occurred_at);
