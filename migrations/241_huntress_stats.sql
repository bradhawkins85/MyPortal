-- Snapshot tables for the Huntress integration. Data is refreshed by the
-- daily `huntress-daily-sync` scheduled job; reports always read from these
-- tables so report rendering never makes live API calls.

CREATE TABLE IF NOT EXISTS huntress_edr_stats (
  company_id INT NOT NULL PRIMARY KEY,
  active_incidents INT NOT NULL DEFAULT 0,
  resolved_incidents INT NOT NULL DEFAULT 0,
  signals_investigated INT NOT NULL DEFAULT 0,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_huntress_edr_stats_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS huntress_itdr_stats (
  company_id INT NOT NULL PRIMARY KEY,
  signals_investigated INT NOT NULL DEFAULT 0,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_huntress_itdr_stats_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS huntress_siem_stats (
  company_id INT NOT NULL PRIMARY KEY,
  data_collected_bytes_30d BIGINT NOT NULL DEFAULT 0,
  window_start DATETIME(6) NULL,
  window_end DATETIME(6) NULL,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_huntress_siem_stats_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS huntress_soc_stats (
  company_id INT NOT NULL PRIMARY KEY,
  total_events_analysed BIGINT NOT NULL DEFAULT 0,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_huntress_soc_stats_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS huntress_sat_stats (
  company_id INT NOT NULL PRIMARY KEY,
  avg_completion_rate DECIMAL(6,2) NOT NULL DEFAULT 0,
  avg_score DECIMAL(6,2) NOT NULL DEFAULT 0,
  phishing_clicks INT NOT NULL DEFAULT 0,
  phishing_compromises INT NOT NULL DEFAULT 0,
  phishing_reports INT NOT NULL DEFAULT 0,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_huntress_sat_stats_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS huntress_sat_learner_assignments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  learner_external_id VARCHAR(128) NOT NULL,
  learner_email VARCHAR(255) DEFAULT NULL,
  learner_name VARCHAR(255) DEFAULT NULL,
  assignment_id VARCHAR(128) NOT NULL,
  assignment_name VARCHAR(255) DEFAULT NULL,
  status VARCHAR(64) DEFAULT NULL,
  completion_percent DECIMAL(6,2) NOT NULL DEFAULT 0,
  score DECIMAL(6,2) NOT NULL DEFAULT 0,
  click_rate DECIMAL(6,2) NOT NULL DEFAULT 0,
  compromise_rate DECIMAL(6,2) NOT NULL DEFAULT 0,
  report_rate DECIMAL(6,2) NOT NULL DEFAULT 0,
  snapshot_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY huntress_sat_learner_unique (company_id, learner_external_id, assignment_id),
  KEY huntress_sat_learner_company (company_id),
  CONSTRAINT fk_huntress_sat_learner_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
