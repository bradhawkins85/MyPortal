CREATE TABLE IF NOT EXISTS company_email_domains (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    company_id INT NOT NULL,
    domain VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_company_email_domains_company FOREIGN KEY (company_id)
        REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT uq_company_email_domains_domain UNIQUE KEY (domain),
    INDEX idx_company_email_domains_company (company_id)
);
