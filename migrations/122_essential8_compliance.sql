-- Essential 8 Compliance Tracking Tables

-- Main table to store Essential 8 controls
CREATE TABLE IF NOT EXISTS essential8_controls (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  control_order INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY unique_control_order (control_order)
);

-- Insert the 8 Essential 8 controls
INSERT INTO essential8_controls (name, description, control_order) VALUES
  ('Application Control', 'Prevent execution of unapproved/malicious programs including .exe, DLL, scripts (e.g. Windows Script Host, PowerShell and HTA) and installers.', 1),
  ('Patch Applications', 'Security vulnerabilities in applications can be exploited to compromise systems. Patching applications mitigates this risk.', 2),
  ('Configure Microsoft Office Macro Settings', 'Microsoft Office macros can be used to deliver and execute malicious code on systems.', 3),
  ('User Application Hardening', 'Web browsers and PDF viewers can be used to access malicious web services or open malicious files.', 4),
  ('Restrict Administrative Privileges', 'Administrative privileges provide adversaries with almost unlimited control over systems.', 5),
  ('Patch Operating Systems', 'Security vulnerabilities in operating systems can be exploited to compromise systems.', 6),
  ('Multi-factor Authentication', 'Weak or stolen user credentials can be used to gain access to systems and information.', 7),
  ('Regular Backups', 'If data is lost or corrupted, or systems are made unavailable, it may not be possible to restore them and continue business operations.', 8);

-- Company-specific compliance tracking
CREATE TABLE IF NOT EXISTS company_essential8_compliance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  control_id INT NOT NULL,
  status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant') DEFAULT 'not_started',
  maturity_level ENUM('ml0', 'ml1', 'ml2', 'ml3') DEFAULT 'ml0',
  evidence TEXT,
  notes TEXT,
  last_reviewed_date DATE,
  target_compliance_date DATE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (control_id) REFERENCES essential8_controls(id) ON DELETE CASCADE,
  UNIQUE KEY unique_company_control (company_id, control_id)
);

-- Audit trail for compliance changes
CREATE TABLE IF NOT EXISTS company_essential8_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  compliance_id INT NOT NULL,
  company_id INT NOT NULL,
  control_id INT NOT NULL,
  user_id INT,
  action VARCHAR(50) NOT NULL,
  old_status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant'),
  new_status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant'),
  old_maturity_level ENUM('ml0', 'ml1', 'ml2', 'ml3'),
  new_maturity_level ENUM('ml0', 'ml1', 'ml2', 'ml3'),
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (compliance_id) REFERENCES company_essential8_compliance(id) ON DELETE CASCADE,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (control_id) REFERENCES essential8_controls(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_company_id (company_id),
  INDEX idx_control_id (control_id),
  INDEX idx_created_at (created_at)
);
