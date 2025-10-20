CREATE TABLE IF NOT EXISTS knowledge_base_sections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    article_id INT NOT NULL,
    position INT NOT NULL,
    heading VARCHAR(255) NULL,
    content LONGTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_kb_sections_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_kb_sections_article_position ON knowledge_base_sections (article_id, position);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT id AS article_id,
       1 AS position,
       NULL AS heading,
       content AS content
FROM knowledge_base_articles
WHERE content IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM knowledge_base_sections s
    WHERE s.article_id = knowledge_base_articles.id
);
