CREATE TABLE IF NOT EXISTS knowledge_base_articles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(191) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    summary TEXT NULL,
    content LONGTEXT NOT NULL,
    permission_scope ENUM('anonymous','user','company','company_admin','super_admin') NOT NULL DEFAULT 'anonymous',
    is_published TINYINT(1) NOT NULL DEFAULT 0,
    published_at DATETIME(6) NULL,
    created_by INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_knowledge_base_articles_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_base_article_users (
    article_id INT NOT NULL,
    user_id INT NOT NULL,
    PRIMARY KEY (article_id, user_id),
    CONSTRAINT fk_kb_article_users_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_article_users_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_base_article_companies (
    article_id INT NOT NULL,
    company_id INT NOT NULL,
    require_admin TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (article_id, company_id, require_admin),
    CONSTRAINT fk_kb_article_companies_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_article_companies_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_kb_articles_published_scope ON knowledge_base_articles (is_published, permission_scope);
CREATE INDEX idx_kb_articles_updated_at ON knowledge_base_articles (updated_at);
