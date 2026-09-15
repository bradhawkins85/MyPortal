-- Daily Microsoft 365 security posture history (all dates/times are UTC).
CREATE TABLE IF NOT EXISTS m365_best_practice_daily_history (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    company_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    pass_count INT NOT NULL DEFAULT 0,
    fail_count INT NOT NULL DEFAULT 0,
    unknown_count INT NOT NULL DEFAULT 0,
    not_applicable_count INT NOT NULL DEFAULT 0,
    secure_score DECIMAL(10,2),
    secure_score_max DECIMAL(10,2),
    secure_score_percentage DECIMAL(6,2),
    recorded_at DATETIME NOT NULL,
    CONSTRAINT fk_m365_bp_history_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_m365_bp_history_company_date
    ON m365_best_practice_daily_history (company_id, snapshot_date);

CREATE INDEX IF NOT EXISTS idx_m365_bp_history_company_recorded
    ON m365_best_practice_daily_history (company_id, recorded_at);
