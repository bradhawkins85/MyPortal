-- Global product/service recommendations for Essential 8 requirements.
CREATE TABLE IF NOT EXISTS essential8_requirement_marketing_pages (
    requirement_id INT NOT NULL PRIMARY KEY,
    marketing_page_id INT NULL,
    recommendation_name VARCHAR(255) NULL,
    external_url VARCHAR(2048) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_e8_recommendation_requirement
        FOREIGN KEY (requirement_id) REFERENCES essential8_requirements(id) ON DELETE CASCADE,
    CONSTRAINT fk_e8_recommendation_marketing_page
        FOREIGN KEY (marketing_page_id) REFERENCES marketing_pages(id) ON DELETE SET NULL
);

ALTER TABLE essential8_requirement_marketing_pages
    ADD COLUMN IF NOT EXISTS recommendation_name VARCHAR(255) NULL AFTER marketing_page_id,
    ADD COLUMN IF NOT EXISTS external_url VARCHAR(2048) NULL AFTER recommendation_name;
