-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 579: RMM maintenance windows and alert snoozes.
--
-- While a window covers a device, its checks still record their results but
-- hold their actions (tickets, scripts, Chocolatey installs), and event log
-- rules raise no tickets for it. A window covers every company (company_id
-- NULL, super admins only), one company or one device, and optionally a
-- single check. It is either one-off (starts_at to ends_at, UTC) or weekly
-- (weekdays, local start_time and duration_minutes in timezone). Snoozing a
-- device or check from the asset page creates a one-off window.

CREATE TABLE IF NOT EXISTS rmm_maintenance_windows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    reason TEXT NULL,
    company_id INT NULL,
    asset_id INT NULL,
    check_id INT NULL,
    schedule_type VARCHAR(16) NOT NULL DEFAULT 'once',
    starts_at DATETIME NULL,
    ends_at DATETIME NULL,
    weekdays VARCHAR(32) NULL,
    start_time CHAR(5) NULL,
    duration_minutes INT NULL,
    timezone VARCHAR(64) NULL,
    is_snooze TINYINT(1) NOT NULL DEFAULT 0,
    is_enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_by_user_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_rmm_maintenance_windows_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_rmm_maintenance_windows_asset FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT fk_rmm_maintenance_windows_check FOREIGN KEY (check_id) REFERENCES rmm_checks(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_rmm_maintenance_windows_company ON rmm_maintenance_windows (company_id, is_enabled);
CREATE INDEX idx_rmm_maintenance_windows_asset ON rmm_maintenance_windows (asset_id);
