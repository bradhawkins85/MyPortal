-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Multi-turn clarification conversations for the AI search agent.
--
-- A conversation groups the original question with any follow-up refinements
-- and the agent's clarifying questions / answers. When the first pass of a
-- query returns thin or ambiguous evidence, the agent asks one focused
-- clarifying question; the user's reply is appended as a new turn and the
-- combined intent is re-searched to produce a grounded answer.
CREATE TABLE agent_conversations (
    id CHAR(36) NOT NULL PRIMARY KEY,
    user_id INT NOT NULL,
    company_id INT NULL,
    original_query TEXT NOT NULL,
    source_filters TEXT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_agent_conversations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_agent_conversations_user ON agent_conversations (user_id, updated_at);

CREATE TABLE agent_conversation_turns (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    turn_number INT NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    payload JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_conversation_turn UNIQUE (conversation_id, turn_number),
    CONSTRAINT fk_agent_conversation_turns_conversation
        FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_agent_conversation_turns_conv
    ON agent_conversation_turns (conversation_id, turn_number);
