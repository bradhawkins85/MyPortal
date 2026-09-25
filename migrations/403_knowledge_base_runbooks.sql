-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

ALTER TABLE knowledge_base_articles
    ADD COLUMN owner_id INT NULL AFTER created_by,
    ADD COLUMN lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'draft' AFTER is_published,
    ADD COLUMN review_due_at DATETIME NULL AFTER lifecycle_status,
    ADD CONSTRAINT fk_kb_article_owner FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL;

UPDATE knowledge_base_articles
SET lifecycle_status = CASE WHEN is_published = 1 THEN 'published' ELSE 'draft' END;

CREATE TABLE IF NOT EXISTS knowledge_base_article_assets (
    article_id INT NOT NULL,
    asset_id INT NOT NULL,
    PRIMARY KEY (article_id, asset_id),
    CONSTRAINT fk_kb_article_asset_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_article_asset_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_base_article_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    article_id INT NOT NULL,
    version_number INT NOT NULL,
    snapshot_json LONGTEXT NOT NULL,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_kb_article_version (article_id, version_number),
    CONSTRAINT fk_kb_version_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_version_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knowledge_base_article_attachments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    article_id INT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    content_type VARCHAR(191) NULL,
    storage_path VARCHAR(500) NOT NULL,
    file_size BIGINT NOT NULL,
    uploaded_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_kb_attachment_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_attachment_user FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_kb_article_lifecycle ON knowledge_base_articles (lifecycle_status, is_published);
CREATE INDEX idx_kb_article_review_due ON knowledge_base_articles (review_due_at);
