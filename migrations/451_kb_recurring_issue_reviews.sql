-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS knowledge_base_recurring_issue_reviews (
    anchor_ticket_id INT NOT NULL PRIMARY KEY,
    ignored TINYINT(1) NOT NULL DEFAULT 0,
    article_id INT NULL UNIQUE,
    ticket_ids TEXT NULL,
    updated_by INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_kb_recurring_review_ticket FOREIGN KEY (anchor_ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_recurring_review_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE SET NULL,
    CONSTRAINT fk_kb_recurring_review_user FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
