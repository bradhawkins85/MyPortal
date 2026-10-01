-- Reusable Defender exclusion lists can be assigned to many companies. Changes
-- to a list are immediately reflected in every assigned company's tray policy.
CREATE TABLE IF NOT EXISTS defender_exclusion_lists (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_defender_exclusion_list_name (name),
  CONSTRAINT fk_defender_exclusion_list_user FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS defender_exclusion_list_items (
  id INT PRIMARY KEY AUTO_INCREMENT,
  exclusion_list_id INT NOT NULL,
  exclusion_type VARCHAR(16) NOT NULL,
  value VARCHAR(1000) NOT NULL,
  CONSTRAINT fk_defender_exclusion_list_item FOREIGN KEY (exclusion_list_id) REFERENCES defender_exclusion_lists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS defender_exclusion_list_companies (
  exclusion_list_id INT NOT NULL,
  company_id INT NOT NULL,
  PRIMARY KEY (exclusion_list_id, company_id),
  CONSTRAINT fk_defender_exclusion_list_assignment FOREIGN KEY (exclusion_list_id) REFERENCES defender_exclusion_lists(id) ON DELETE CASCADE,
  CONSTRAINT fk_defender_exclusion_list_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
