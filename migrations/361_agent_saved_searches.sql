CREATE TABLE IF NOT EXISTS agent_saved_searches (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    user_id INTEGER NOT NULL,
    name VARCHAR(120) NOT NULL,
    query_text TEXT NOT NULL,
    source_filters TEXT NULL,
    is_shared INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_saved_searches_user_name
ON agent_saved_searches(user_id, name);

CREATE INDEX IF NOT EXISTS idx_agent_saved_searches_shared
ON agent_saved_searches(is_shared, updated_at);
