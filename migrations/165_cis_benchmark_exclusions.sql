CREATE TABLE IF NOT EXISTS cis_benchmark_exclusions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    reason VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_benchmark_exclusion (company_id, check_id),
    INDEX idx_exclusion_company (company_id)
);
