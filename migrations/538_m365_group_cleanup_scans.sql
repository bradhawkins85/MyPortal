-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Caches the latest Office 365 group cleanup scan per company: groups and
-- distribution lists that still contain disabled accounts or ex-staff.

CREATE TABLE IF NOT EXISTS m365_group_cleanup_scans (
  company_id BIGINT PRIMARY KEY,
  scanned_at DATETIME NOT NULL,
  result LONGTEXT NULL,
  updated_at DATETIME NOT NULL
);
