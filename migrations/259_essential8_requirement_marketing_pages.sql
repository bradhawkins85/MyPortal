CREATE TABLE IF NOT EXISTS essential8_requirement_marketing_pages (
    requirement_id INT NOT NULL PRIMARY KEY,
    marketing_page_id INT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_e8_requirement_marketing_pages_page (marketing_page_id),
    CONSTRAINT fk_e8_requirement_marketing_requirement FOREIGN KEY (requirement_id) REFERENCES essential8_requirements(id) ON DELETE CASCADE,
    CONSTRAINT fk_e8_requirement_marketing_page FOREIGN KEY (marketing_page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
