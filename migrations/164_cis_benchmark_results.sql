CREATE TABLE IF NOT EXISTS cis_benchmark_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    benchmark_category VARCHAR(100) NOT NULL,
    check_id VARCHAR(100) NOT NULL,
    check_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    details TEXT,
    run_at DATETIME NOT NULL,
    UNIQUE KEY uq_benchmark_check (company_id, benchmark_category, check_id),
    INDEX idx_benchmark_company (company_id),
    INDEX idx_benchmark_run_at (company_id, run_at),
    CONSTRAINT chk_benchmark_status CHECK (status IN ('pass', 'fail', 'unknown', 'not_applicable'))
);
