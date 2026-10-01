-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS application_types (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_application_type_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT uq_application_type_name UNIQUE (company_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS applications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(191) NOT NULL,
    type_id INT NULL,
    version VARCHAR(100) NULL,
    business_impact TEXT NULL,
    importance VARCHAR(16) NOT NULL DEFAULT 'medium',
    champion_staff_id INT NULL,
    champion_name VARCHAR(255) NULL,
    product_key_encrypted TEXT NULL,
    notes TEXT NULL,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_application_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_application_type FOREIGN KEY (type_id) REFERENCES application_types(id) ON DELETE SET NULL,
    CONSTRAINT fk_application_champion FOREIGN KEY (champion_staff_id) REFERENCES staff(id) ON DELETE SET NULL,
    CONSTRAINT fk_application_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS application_kb_links (
    application_id INT NOT NULL, article_id INT NOT NULL,
    PRIMARY KEY (application_id, article_id),
    CONSTRAINT fk_application_kb_application FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE,
    CONSTRAINT fk_application_kb_article FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS application_external_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    application_id INT NOT NULL,
    title VARCHAR(255) NULL,
    url VARCHAR(2048) NOT NULL,
    position INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_application_link_application FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
);

CREATE INDEX idx_applications_company ON applications (company_id, name);
CREATE INDEX idx_application_links_application ON application_external_links (application_id, position);
