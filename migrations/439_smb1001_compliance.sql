-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 439: SMB1001 compliance tracking, replacing the retiring Essential 8
-- framework on the Compliance (now SMB1001) page. SMB1001 is a cumulative,
-- five-tier certification standard (Bronze to Diamond) split into five domains.
-- Essential 8 tables and data are left untouched so historical records remain
-- available and can be converted into SMB1001 progress per company.

CREATE TABLE IF NOT EXISTS smb1001_tiers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  tier_level INT NOT NULL,
  code VARCHAR(32) NOT NULL,
  name VARCHAR(64) NOT NULL,
  description TEXT,
  attestation VARCHAR(32) NOT NULL DEFAULT 'self',
  CONSTRAINT uq_smb1001_tier_level UNIQUE (tier_level),
  CONSTRAINT uq_smb1001_tier_code UNIQUE (code)
);

CREATE TABLE IF NOT EXISTS smb1001_controls (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  tier_level INT NOT NULL,
  domain VARCHAR(32) NOT NULL,
  control_order INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT NOT NULL,
  verification TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT uq_smb1001_control_code UNIQUE (code)
);

CREATE INDEX idx_smb1001_control_tier ON smb1001_controls (tier_level, control_order);

CREATE TABLE IF NOT EXISTS company_smb1001_profile (
  company_id INT NOT NULL PRIMARY KEY,
  target_tier INT NOT NULL DEFAULT 1,
  essential8_imported_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_smb1001_profile_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_smb1001_compliance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  control_id INT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'not_started',
  evidence TEXT,
  notes TEXT,
  owner_user_id INT NULL,
  last_reviewed_date DATE NULL,
  target_compliance_date DATE NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'manual',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_smb1001_compliance_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_compliance_control FOREIGN KEY (control_id) REFERENCES smb1001_controls(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_compliance_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT uq_company_smb1001_control UNIQUE (company_id, control_id)
);

CREATE INDEX idx_company_smb1001_status ON company_smb1001_compliance (company_id, status);

CREATE TABLE IF NOT EXISTS company_smb1001_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  control_id INT NOT NULL,
  user_id INT NULL,
  action VARCHAR(64) NOT NULL,
  from_status VARCHAR(32) NULL,
  to_status VARCHAR(32) NULL,
  change_summary TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_smb1001_audit_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_audit_control FOREIGN KEY (control_id) REFERENCES smb1001_controls(id) ON DELETE CASCADE,
  CONSTRAINT fk_smb1001_audit_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_company_smb1001_audit ON company_smb1001_audit (company_id, control_id, created_at);

INSERT INTO smb1001_tiers (tier_level, code, name, description, attestation) VALUES
  (1, 'bronze', 'Bronze', 'Foundational protections: IT support, firewalls, endpoint protection, automatic updates, default passwords, backups and basic awareness.', 'self'),
  (2, 'silver', 'Silver', 'Stronger identity and email protection, access control disciplines, basic incident response and structured backups.', 'self'),
  (3, 'gold', 'Gold', 'EDR, email authentication, MFA across business systems, documented plans, asset register, AI use policy and regular training.', 'self'),
  (4, 'platinum', 'Platinum', 'Higher assurance: phishing-resistant MFA, vulnerability scanning, recovery testing, support commitments and independent verification.', 'independent'),
  (5, 'diamond', 'Diamond', 'Advanced governance: application control, encryption, managed detection and response, penetration and social engineering testing, supplier assurance.', 'independent');

INSERT INTO smb1001_controls (code, tier_level, domain, control_order, name, description, verification) VALUES
-- Tier 1: Bronze
  ('TM-01', 1, 'technology', 1, 'Engage competent IT support', 'The business has a managed service provider or competent IT contractor engaged to support, maintain and secure its IT systems.', 'A current support agreement or engagement letter exists and staff know how to contact IT support.'),
  ('TM-02', 1, 'technology', 2, 'Firewalls on the network and devices', 'A firewall protects the edge of the business network and the software firewall is enabled on every computer.', 'Router/firewall configuration is documented and the host firewall is enabled on all workstations and servers.'),
  ('TM-03', 1, 'technology', 3, 'Antivirus on every device', 'Antivirus or endpoint protection is installed on every device and set to update automatically.', 'Endpoint protection console shows every device protected with current definitions.'),
  ('TM-04', 1, 'technology', 4, 'Automatic security updates', 'Operating systems and applications are configured to install security updates automatically.', 'Patch or RMM reporting shows automatic updates enabled and devices up to date.'),
  ('AM-01', 1, 'access', 1, 'Change default passwords', 'Default or factory passwords are changed on all devices, network equipment and software before use.', 'Routers, printers, NAS, cameras and software admin accounts have been checked for default credentials.'),
  ('BR-01', 1, 'backup', 1, 'Automated backups of important data', 'Important business data is backed up automatically and regularly, with a copy stored separately from the primary systems (for example off-site or in the cloud).', 'Backup job history shows successful, regular backups including an off-site or cloud copy.'),
  ('ET-01', 1, 'education', 1, 'Basic cyber security awareness', 'Staff are made aware of common cyber threats such as scams, phishing and fraudulent payment requests.', 'Awareness material or a briefing has been delivered to all staff.'),
-- Tier 2: Silver
  ('TM-05', 2, 'technology', 5, 'Remove unsupported software', 'Operating systems and applications that no longer receive security updates from the vendor are removed or replaced.', 'Device inventory shows no end-of-life operating systems or unsupported applications.'),
  ('TM-06', 2, 'technology', 6, 'Secure and separate Wi-Fi', 'Business Wi-Fi uses strong encryption (WPA2/WPA3) and guest or visitor access is separated from the business network.', 'Wireless configuration shows WPA2/WPA3 and a separate guest network or VLAN.'),
  ('AM-02', 2, 'access', 2, 'Password manager', 'Staff use a business password manager to create and store strong, unique passwords.', 'A password manager is deployed and staff accounts are enrolled.'),
  ('AM-03', 2, 'access', 3, 'MFA on email accounts', 'Multi-factor authentication is enforced on all email accounts.', 'Email platform (e.g. Microsoft 365 / Google Workspace) reports MFA enforced for every mailbox user.'),
  ('AM-04', 2, 'access', 4, 'Unique user accounts', 'Every person has their own user account and shared logins are not used for business systems.', 'User list reviewed and no generic shared accounts are used for sign-in.'),
  ('AM-05', 2, 'access', 5, 'Restrict administrator access', 'Administrator privileges are limited to staff who need them and are held in separate admin accounts that are not used for everyday work.', 'Admin group membership reviewed and admin accounts are separate from daily-use accounts.'),
  ('AM-06', 2, 'access', 6, 'Remove access when staff leave', 'Accounts and access are disabled promptly when staff leave or change roles.', 'An offboarding checklist exists and recent leavers have disabled accounts.'),
  ('BR-02', 2, 'backup', 2, 'Protected backups', 'Backups are encrypted and at least one copy is kept offline or immutable so it cannot be altered by ransomware.', 'Backup settings show encryption and an offline or immutable copy.'),
  ('PP-01', 2, 'policies', 1, 'Incident reporting procedure', 'A simple documented procedure tells staff how to report a suspected cyber incident and who to contact, including ReportCyber.', 'The procedure is documented and accessible to all staff.'),
  ('PP-02', 2, 'policies', 2, 'Access register', 'A register records who has access to each business system and is reviewed regularly.', 'Access register exists and shows a recent review date.'),
  ('ET-02', 2, 'education', 2, 'Phishing awareness', 'Staff are trained to recognise phishing emails and know how to report suspicious messages.', 'Training records show staff completion and a report-phishing process exists.'),
-- Tier 3: Gold
  ('TM-07', 3, 'technology', 7, 'Endpoint detection and response (EDR)', 'An EDR solution is deployed on all computers and servers.', 'EDR console shows every device enrolled and reporting.'),
  ('TM-08', 3, 'technology', 8, 'Email authentication (SPF, DKIM, DMARC)', 'SPF and DKIM are configured for business email domains and DMARC is set to an enforcing policy (quarantine or reject).', 'DNS records show SPF, DKIM and a DMARC policy of p=quarantine or p=reject.'),
  ('TM-09', 3, 'technology', 9, 'Restrict Office macros', 'Microsoft Office macros are disabled for users without a business need and macros from the internet are blocked.', 'Group Policy/Intune settings block internet macros and restrict macro use.'),
  ('TM-10', 3, 'technology', 10, 'Harden web browsers and applications', 'Web browsers, PDF readers and office applications are hardened, for example blocking ads, unneeded plugins and risky content.', 'Browser and application hardening policies are applied to all devices.'),
  ('AM-07', 3, 'access', 7, 'MFA on all business systems', 'Multi-factor authentication is enforced on all business applications, cloud services and remote access.', 'MFA is enforced for finance, line of business, cloud portals and VPN/remote access.'),
  ('AM-08', 3, 'access', 8, 'Privileged accounts isolated from risky activity', 'Privileged accounts cannot browse the web, read email or access the internet except where explicitly required.', 'Policy prevents admin accounts from web browsing and email.'),
  ('BR-03', 3, 'backup', 3, 'Test backup restores', 'Restoring data from backups is tested at least annually and results are recorded.', 'A recent restore test record shows successful recovery.'),
  ('PP-03', 3, 'policies', 3, 'Cyber incident response plan', 'A written incident response plan defines roles, contacts and steps to contain and recover from an incident, and is reviewed annually.', 'The incident response plan is documented and shows a review date within the last 12 months.'),
  ('PP-04', 3, 'policies', 4, 'Digital asset register', 'A register of hardware, software, cloud services and important data is maintained.', 'Asset register is current and includes owners for each asset.'),
  ('PP-05', 3, 'policies', 5, 'Cyber insurance', 'The business holds cyber insurance or has formally assessed and recorded its decision not to.', 'Policy schedule or documented risk decision is on file.'),
  ('PP-06', 3, 'policies', 6, 'Responsible AI use policy', 'A policy defines how staff may use AI tools, including what business or customer data must not be entered.', 'AI use policy is documented and communicated to staff.'),
  ('PP-07', 3, 'policies', 7, 'Acceptable use and security policy', 'An information security / acceptable use policy is documented and acknowledged by staff.', 'Signed or recorded staff acknowledgements are on file.'),
  ('ET-03', 3, 'education', 3, 'Regular security training', 'All staff complete cyber security awareness training at least annually and completion is recorded.', 'Training platform shows annual completion for all staff.'),
-- Tier 4: Platinum
  ('TM-11', 4, 'technology', 11, 'Vulnerability scanning', 'Internet-facing and internal systems are scanned for vulnerabilities at least monthly and findings are remediated.', 'Scan reports and a remediation log are available.'),
  ('TM-12', 4, 'technology', 12, 'Centralised security logging', 'Security events from key systems are collected centrally, retained and reviewed.', 'Log platform shows sources connected and a review process.'),
  ('AM-09', 4, 'access', 9, 'Phishing-resistant MFA', 'Phishing-resistant MFA (e.g. FIDO2 security keys, passkeys or Windows Hello for Business) is used for administrator and email accounts.', 'Authentication methods policy enforces phishing-resistant methods for these accounts.'),
  ('BR-04', 4, 'backup', 4, 'Recovery plan and full recovery testing', 'A documented backup and recovery plan defines recovery objectives, and full system recovery is tested.', 'Recovery plan with RTO/RPO and a recent full recovery test report.'),
  ('PP-08', 4, 'policies', 8, 'IT support service commitments', 'IT support arrangements include defined response times for security incidents.', 'Support agreement specifies incident response SLAs.'),
  ('PP-09', 4, 'policies', 9, 'Business continuity plan', 'A business continuity plan covers cyber incidents and is tested.', 'BCP is documented, includes cyber scenarios and has a recorded test.'),
  ('PP-10', 4, 'policies', 10, 'Independent verification', 'Controls have been audited by an independent verification organisation, as required for Platinum and Diamond certification.', 'Independent audit report or certificate is on file.'),
  ('ET-04', 4, 'education', 4, 'Phishing simulations', 'Simulated phishing exercises are run at least twice a year and results are used to target training.', 'Simulation campaign results for the last 12 months.'),
-- Tier 5: Diamond
  ('TM-13', 5, 'technology', 13, 'Application control', 'Application control restricts execution of programs, scripts and installers to an approved set on workstations and servers.', 'Application control policy is enforced and blocked executions are logged.'),
  ('TM-14', 5, 'technology', 14, 'Encryption of devices and data', 'Full disk encryption is enabled on all endpoints and business data is encrypted at rest and in transit.', 'Encryption compliance report shows all devices encrypted.'),
  ('TM-15', 5, 'technology', 15, 'Managed detection and response (MDR)', 'Security alerts are monitored and responded to 24/7 by a managed detection and response service or SOC.', 'MDR/SOC service agreement and escalation contacts are on file.'),
  ('TM-16', 5, 'technology', 16, 'Penetration testing', 'An independent penetration test is carried out at least annually and findings are remediated.', 'Penetration test report and remediation tracking.'),
  ('PP-11', 5, 'policies', 11, 'Supplier security assurance', 'The security of critical suppliers and service providers is assessed and reviewed.', 'Supplier register with security assessments for critical suppliers.'),
  ('PP-12', 5, 'policies', 12, 'Security governance and risk management', 'Cyber security risks are recorded in a risk register and reviewed by management at least quarterly.', 'Risk register and minutes of management reviews.'),
  ('ET-05', 5, 'education', 5, 'Social engineering testing', 'Social engineering testing beyond email (e.g. phone, SMS or physical) is carried out and followed up with training.', 'Test report and follow-up training records.');
