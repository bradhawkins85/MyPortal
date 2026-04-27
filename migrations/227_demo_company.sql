-- Demo Company Seeding Support
-- Adds is_demo flag to companies and a log table to track one-time demo seeding.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS demo_seed_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  seeded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  removed_at DATETIME NULL,
  company_id INT NULL,
  seeded_by_user_id INT NULL,
  note VARCHAR(500) NULL,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL,
  FOREIGN KEY (seeded_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
