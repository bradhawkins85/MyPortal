-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Controlled AI tag vocabulary: merges variant tag slugs into a canonical slug.
CREATE TABLE IF NOT EXISTS tag_synonyms (
    id INT AUTO_INCREMENT PRIMARY KEY,
    variant_slug VARCHAR(48) NOT NULL UNIQUE,
    canonical_slug VARCHAR(48) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    created_by INT NULL,
    INDEX idx_tag_synonyms_canonical (canonical_slug),
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);
