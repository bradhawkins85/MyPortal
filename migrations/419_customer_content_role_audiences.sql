-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

CREATE TABLE IF NOT EXISTS customer_content_role_audience (
    company_id INT NOT NULL,
    content_type VARCHAR(32) NOT NULL,
    record_id INT NOT NULL,
    role_id INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company_id, content_type, record_id, role_id),
    CONSTRAINT fk_customer_content_audience_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_customer_content_audience_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
);

-- Preserve the exact set of existing company members: roles that already exist
-- are grandfathered, while roles created after this migration remain denied.
INSERT IGNORE INTO customer_content_role_audience (company_id, content_type, record_id, role_id)
SELECT a.company_id, 'asset', a.id, r.id
FROM assets a CROSS JOIN roles r
WHERE a.customer_visible = 1;

INSERT IGNORE INTO customer_content_role_audience (company_id, content_type, record_id, role_id)
SELECT DISTINCT ac.company_id, 'knowledge_base', ac.article_id, r.id
FROM knowledge_base_article_companies ac
CROSS JOIN roles r
JOIN knowledge_base_articles a ON a.id = ac.article_id
WHERE a.is_published = 1;

CREATE INDEX idx_customer_content_role_lookup
    ON customer_content_role_audience (content_type, record_id, company_id);
