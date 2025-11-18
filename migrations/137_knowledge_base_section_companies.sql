-- Add company-level permissions for knowledge base sections
-- This allows admins to restrict which companies can view specific sections

CREATE TABLE IF NOT EXISTS knowledge_base_section_companies (
    section_id INT NOT NULL,
    company_id INT NOT NULL,
    PRIMARY KEY (section_id, company_id),
    CONSTRAINT fk_kb_section_companies_section FOREIGN KEY (section_id) REFERENCES knowledge_base_sections(id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_section_companies_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_kb_section_companies_section ON knowledge_base_section_companies (section_id);
CREATE INDEX IF NOT EXISTS idx_kb_section_companies_company ON knowledge_base_section_companies (company_id);
