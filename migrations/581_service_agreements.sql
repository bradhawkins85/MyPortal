-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 581: service contracts and agreements.
--
-- A service agreement records what a company's contract covers: unlimited
-- support (managed services), a number of hours each month, quarter or year,
-- or a one-off prepaid block of hours, optionally limited to some labour
-- types. Usage is worked out from ticket time entries, so agreements track
-- coverage, drawdown, overage and profitability without changing invoicing.
-- service_agreement_alerts remembers which usage and expiry alerts have been
-- raised so each fires once per period.

CREATE TABLE IF NOT EXISTS service_agreements (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    agreement_type VARCHAR(16) NOT NULL DEFAULT 'hours',
    hours_allowance DECIMAL(10,2) NULL,
    period VARCHAR(16) NOT NULL DEFAULT 'monthly',
    start_date DATE NOT NULL,
    end_date DATE NULL,
    fee_amount DECIMAL(12,2) NULL,
    alert_percent INT NOT NULL DEFAULT 80,
    notes TEXT NULL,
    is_cancelled TINYINT(1) NOT NULL DEFAULT 0,
    created_by_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_service_agreements_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_service_agreements_company ON service_agreements (company_id, is_cancelled);

CREATE TABLE IF NOT EXISTS service_agreement_labour_types (
    agreement_id INT NOT NULL,
    labour_type_id INT NOT NULL,
    PRIMARY KEY (agreement_id, labour_type_id),
    CONSTRAINT fk_service_agreement_labour_agreement FOREIGN KEY (agreement_id) REFERENCES service_agreements(id) ON DELETE CASCADE,
    CONSTRAINT fk_service_agreement_labour_type FOREIGN KEY (labour_type_id) REFERENCES ticket_labour_types(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS service_agreement_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agreement_id INT NOT NULL,
    kind VARCHAR(16) NOT NULL,
    period_start DATE NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_service_agreement_alerts UNIQUE (agreement_id, kind, period_start),
    CONSTRAINT fk_service_agreement_alerts_agreement FOREIGN KEY (agreement_id) REFERENCES service_agreements(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
