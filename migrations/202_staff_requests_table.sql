CREATE TABLE IF NOT EXISTS staff_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NULL,
    mobile_phone VARCHAR(50) NULL,
    date_onboarded DATETIME NULL,
    department VARCHAR(255) NULL,
    job_title VARCHAR(255) NULL,
    request_notes TEXT NULL,
    custom_fields_json TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    requested_by_user_id INT NULL,
    requested_at DATETIME NULL,
    approved_by_user_id INT NULL,
    approved_at DATETIME NULL,
    approval_notes TEXT NULL,
    staff_id INT NULL COMMENT 'Linked staff record created on approval',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_staff_requests_company_status
    ON staff_requests (company_id, status);

CREATE INDEX IF NOT EXISTS idx_staff_requests_requested_by
    ON staff_requests (requested_by_user_id);

CREATE INDEX IF NOT EXISTS idx_staff_requests_company_updated
    ON staff_requests (company_id, updated_at, id);
