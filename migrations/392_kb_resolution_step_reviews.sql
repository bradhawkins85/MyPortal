CREATE TABLE IF NOT EXISTS knowledge_base_resolution_reviews (
    ticket_id INT NOT NULL PRIMARY KEY,
    ignored TINYINT(1) NOT NULL DEFAULT 0,
    article_id INT NULL UNIQUE,
    updated_by INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_kb_resolution_review_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_resolution_review_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE SET NULL,
    CONSTRAINT fk_kb_resolution_review_user FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_kb_resolution_reviews_ignored ON knowledge_base_resolution_reviews (ignored);
