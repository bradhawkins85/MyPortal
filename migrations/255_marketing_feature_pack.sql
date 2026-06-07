CREATE TABLE IF NOT EXISTS marketing_pages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    slug VARCHAR(96) NOT NULL UNIQUE,
    title VARCHAR(160) NOT NULL,
    subtitle VARCHAR(255) NULL,
    intro_text TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketing_page_sections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id INT NOT NULL,
    title VARCHAR(160) NOT NULL,
    anchor_slug VARCHAR(120) NOT NULL,
    content_text TEXT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_marketing_section_anchor (page_id, anchor_slug),
    INDEX idx_marketing_sections_page_order (page_id, sort_order, id),
    CONSTRAINT fk_marketing_sections_page FOREIGN KEY (page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketing_leads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id INT NOT NULL,
    slug_snapshot VARCHAR(96) NOT NULL,
    page_title_snapshot VARCHAR(160) NOT NULL,
    name VARCHAR(160) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(64) NULL,
    allow_marketing TINYINT(1) NOT NULL DEFAULT 0,
    allow_other_services TINYINT(1) NOT NULL DEFAULT 0,
    ticket_id INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_marketing_leads_page_created (page_id, created_at),
    INDEX idx_marketing_leads_ticket (ticket_id),
    CONSTRAINT fk_marketing_leads_page FOREIGN KEY (page_id) REFERENCES marketing_pages(id) ON DELETE CASCADE,
    CONSTRAINT fk_marketing_leads_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
