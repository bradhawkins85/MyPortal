-- Essential 8 Requirements Table
-- Stores individual requirements for each control at each maturity level

CREATE TABLE IF NOT EXISTS essential8_requirements (
  id INT AUTO_INCREMENT PRIMARY KEY,
  control_id INT NOT NULL,
  maturity_level ENUM('ml1', 'ml2', 'ml3') NOT NULL,
  requirement_order INT NOT NULL,
  description TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (control_id) REFERENCES essential8_controls(id) ON DELETE CASCADE,
  INDEX idx_control_maturity (control_id, maturity_level),
  INDEX idx_requirement_order (control_id, maturity_level, requirement_order)
);

-- Company-specific requirement compliance tracking
CREATE TABLE IF NOT EXISTS company_essential8_requirement_compliance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  requirement_id INT NOT NULL,
  status ENUM('not_started', 'in_progress', 'compliant', 'non_compliant', 'not_applicable') DEFAULT 'not_started',
  evidence TEXT,
  notes TEXT,
  last_reviewed_date DATE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  FOREIGN KEY (requirement_id) REFERENCES essential8_requirements(id) ON DELETE CASCADE,
  UNIQUE KEY unique_company_requirement (company_id, requirement_id),
  INDEX idx_company_status (company_id, status)
);

-- Insert Essential 8 Requirements based on the Essential Eight Maturity Model
-- Control 1: Application Control (Mitigation Strategy 1)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(1, 'ml1', 1, 'An automated method of asset discovery is used at least fortnightly to support the detection of assets for subsequent vulnerability scanning activities.'),
(1, 'ml1', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in internet-facing services.'),
(1, 'ml1', 3, 'A vulnerability scanner is used at least fortnightly to identify missing patches or updates for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products.'),
(1, 'ml1', 4, 'Application control is implemented on all workstations to restrict the execution of executables to an approved set.'),
(1, 'ml1', 5, 'Application control is implemented on all workstations to restrict the execution of software library files to an approved set.'),
(1, 'ml1', 6, 'Application control is implemented on all workstations to restrict the execution of scripts to an approved set.'),
(1, 'ml1', 7, 'Application control is implemented on all workstations to restrict the execution of installers to an approved set.'),
(1, 'ml1', 8, 'Application control is implemented on all internet-facing servers to restrict the execution of executables to an approved set.'),
(1, 'ml1', 9, 'Application control is implemented on all internet-facing servers to restrict the execution of software library files to an approved set.'),
(1, 'ml1', 10, 'Application control is implemented on all internet-facing servers to restrict the execution of scripts to an approved set.'),
(1, 'ml1', 11, 'Application control is implemented on all internet-facing servers to restrict the execution of installers to an approved set.'),
-- ML2 Requirements
(1, 'ml2', 1, 'Application control is implemented on all workstations to restrict the execution of executables, software library files, scripts and installers to an approved set.'),
(1, 'ml2', 2, 'Application control is implemented on all servers to restrict the execution of executables, software library files, scripts and installers to an approved set.'),
(1, 'ml2', 3, 'Application control rulesets are validated on an annual, or more frequent, basis.'),
(1, 'ml2', 4, 'Allowed and blocked executions are logged.'),
(1, 'ml2', 5, 'Event logs are protected from unauthorised modification and deletion.'),
(1, 'ml2', 6, 'Event logs are analysed in a timely manner to detect cyber security events.'),
-- ML3 Requirements
(1, 'ml3', 1, 'Application control is implemented on all workstations using cryptographic hash rules, publisher certificate rules or path rules.'),
(1, 'ml3', 2, 'Application control is implemented on all servers using cryptographic hash rules, publisher certificate rules or path rules.'),
(1, 'ml3', 3, 'Microsoft''s ''recommended block rules'' are implemented.'),
(1, 'ml3', 4, 'Microsoft''s ''recommended driver block rules'' are implemented.'),
(1, 'ml3', 5, 'Application control rulesets are validated on a quarterly, or more frequent, basis.'),
(1, 'ml3', 6, 'Centralised logging of allowed and blocked executions is enabled.'),
(1, 'ml3', 7, 'Event logs are centralised.'),
(1, 'ml3', 8, 'Event logs are monitored for signs of compromise and actioned when cyber security events are detected.');

-- Control 2: Patch Applications (Mitigation Strategy 2)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(2, 'ml1', 1, 'An automated method of asset discovery is used at least fortnightly to support the detection of assets for subsequent vulnerability scanning activities.'),
(2, 'ml1', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in internet-facing services.'),
(2, 'ml1', 3, 'A vulnerability scanner is used at least fortnightly to identify missing patches or updates for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products.'),
(2, 'ml1', 4, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(2, 'ml1', 5, 'Patches, updates or other vendor mitigations for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products are applied within two weeks of release.'),
(2, 'ml1', 6, 'Online services that are no longer supported by vendors are removed.'),
-- ML2 Requirements
(2, 'ml2', 1, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in internet-facing services.'),
(2, 'ml2', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products.'),
(2, 'ml2', 3, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(2, 'ml2', 4, 'Patches, updates or other vendor mitigations for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(2, 'ml2', 5, 'Patches, updates or other vendor mitigations for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products are applied within two weeks of release when vulnerabilities are assessed as moderate or high by vendors.'),
(2, 'ml2', 6, 'The latest release, or the previous release, of applications is used.'),
-- ML3 Requirements
(2, 'ml3', 1, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products.'),
(2, 'ml3', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in other applications.'),
(2, 'ml3', 3, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within two weeks of release when vulnerabilities are assessed as moderate or high by vendors.'),
(2, 'ml3', 4, 'Patches, updates or other vendor mitigations for vulnerabilities in office productivity suites, web browsers and their extensions, email clients, PDF software, and security products are applied within one month of release when vulnerabilities are assessed as low by vendors.'),
(2, 'ml3', 5, 'Patches, updates or other vendor mitigations for vulnerabilities in other applications are applied within one month of release when vulnerabilities are assessed as extreme risk, critical or high by vendors.'),
(2, 'ml3', 6, 'The latest release of applications is used.');

-- Control 3: Configure Microsoft Office Macro Settings (Mitigation Strategy 3)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(3, 'ml1', 1, 'Microsoft Office macros are disabled for users that do not have a demonstrated business requirement.'),
(3, 'ml1', 2, 'Microsoft Office macros in files originating from the internet are blocked.'),
-- ML2 Requirements
(3, 'ml2', 1, 'Microsoft Office macros in files originating from the internet are blocked.'),
(3, 'ml2', 2, 'Microsoft Office macro antivirus scanning is enabled.'),
(3, 'ml2', 3, 'Microsoft Office macro security settings cannot be changed by users.'),
-- ML3 Requirements
(3, 'ml3', 1, 'Microsoft Office macros in files originating from the internet are blocked.'),
(3, 'ml3', 2, 'Microsoft Office macro antivirus scanning is enabled.'),
(3, 'ml3', 3, 'Microsoft Office macros are only allowed to execute in files from Trusted Locations where write access is limited to personnel whose role is to vet and approve macros.'),
(3, 'ml3', 4, 'Microsoft Office macro security settings cannot be changed by users.');

-- Control 4: User Application Hardening (Mitigation Strategy 4)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(4, 'ml1', 1, 'Web browsers are configured to block or disable support for Flash Player content.'),
(4, 'ml1', 2, 'Web browsers are configured to block web advertisements.'),
(4, 'ml1', 3, 'Internet Explorer 11 is disabled or removed.'),
-- ML2 Requirements
(4, 'ml2', 1, 'Web browsers are configured to block or disable support for Flash Player content.'),
(4, 'ml2', 2, 'Web browsers are configured to block web advertisements.'),
(4, 'ml2', 3, 'Web browsers are configured to block Java from the internet.'),
(4, 'ml2', 4, 'Web browser security settings cannot be changed by users.'),
(4, 'ml2', 5, 'Internet Explorer 11 is disabled or removed.'),
-- ML3 Requirements
(4, 'ml3', 1, 'Web browsers are configured to block or disable support for Flash Player content.'),
(4, 'ml3', 2, 'Web browsers are configured to block web advertisements.'),
(4, 'ml3', 3, 'Web browsers are configured to block Java from the internet.'),
(4, 'ml3', 4, 'Web browser security settings cannot be changed by users.'),
(4, 'ml3', 5, '.NET Framework 3.5 (includes .NET 2.0 and 3.0) is disabled or removed.');

-- Control 5: Restrict Administrative Privileges (Mitigation Strategy 5)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(5, 'ml1', 1, 'Requests for privileged access to systems and applications are validated when first requested.'),
(5, 'ml1', 2, 'Privileged accounts (excluding local administrator accounts) are prevented from accessing the internet, email and web services.'),
(5, 'ml1', 3, 'Privileged users use separate privileged and unprivileged operating environments.'),
(5, 'ml1', 4, 'Unprivileged accounts cannot logon to privileged operating environments.'),
-- ML2 Requirements
(5, 'ml2', 1, 'Requests for privileged access to systems and applications are validated when first requested.'),
(5, 'ml2', 2, 'Privileged access to systems and applications is automatically disabled after 12 months unless revalidated.'),
(5, 'ml2', 3, 'Privileged accounts (including local administrator accounts) are prevented from accessing the internet, email and web services.'),
(5, 'ml2', 4, 'Privileged users use separate privileged and unprivileged operating environments.'),
(5, 'ml2', 5, 'Unprivileged accounts cannot logon to privileged operating environments.'),
(5, 'ml2', 6, 'Privileged accounts (excluding local administrator accounts) cannot logon to unprivileged operating environments.'),
(5, 'ml2', 7, 'Just-in-time administration is used for administering systems and applications.'),
-- ML3 Requirements
(5, 'ml3', 1, 'Requests for privileged access to systems and applications are validated when first requested.'),
(5, 'ml3', 2, 'Privileged access to systems and applications is automatically disabled after 12 months unless revalidated.'),
(5, 'ml3', 3, 'Privileged access to systems and applications is automatically disabled after 45 days of inactivity.'),
(5, 'ml3', 4, 'Privileged accounts (including local administrator accounts) are prevented from accessing the internet, email and web services.'),
(5, 'ml3', 5, 'Privileged users use separate privileged and unprivileged operating environments.'),
(5, 'ml3', 6, 'Unprivileged accounts cannot logon to privileged operating environments.'),
(5, 'ml3', 7, 'Privileged accounts (including local administrator accounts) cannot logon to unprivileged operating environments.'),
(5, 'ml3', 8, 'Just-in-time administration is used for administering systems and applications.');

-- Control 6: Patch Operating Systems (Mitigation Strategy 6)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(6, 'ml1', 1, 'An automated method of asset discovery is used at least fortnightly to support the detection of assets for subsequent vulnerability scanning activities.'),
(6, 'ml1', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in internet-facing services.'),
(6, 'ml1', 3, 'A vulnerability scanner is used at least fortnightly to identify missing patches or updates for vulnerabilities in operating systems of workstations, servers and network devices.'),
(6, 'ml1', 4, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(6, 'ml1', 5, 'Patches, updates or other vendor mitigations for vulnerabilities in operating systems of workstations, servers and network devices are applied within two weeks of release.'),
(6, 'ml1', 6, 'Operating systems that are no longer supported by vendors are removed.'),
-- ML2 Requirements
(6, 'ml2', 1, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in internet-facing services.'),
(6, 'ml2', 2, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in operating systems of workstations, servers and network devices.'),
(6, 'ml2', 3, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(6, 'ml2', 4, 'Patches, updates or other vendor mitigations for vulnerabilities in operating systems of workstations, servers and network devices are applied within 48 hours of release when vulnerabilities are assessed as critical by vendors or when working exploits exist.'),
(6, 'ml2', 5, 'Patches, updates or other vendor mitigations for vulnerabilities in operating systems of workstations, servers and network devices are applied within two weeks of release when vulnerabilities are assessed as moderate or high by vendors.'),
(6, 'ml2', 6, 'The latest release, or the previous release, of operating systems is used.'),
-- ML3 Requirements
(6, 'ml3', 1, 'A vulnerability scanner is used at least daily to identify missing patches or updates for vulnerabilities in operating systems of workstations, servers and network devices.'),
(6, 'ml3', 2, 'Patches, updates or other vendor mitigations for vulnerabilities in internet-facing services are applied within two weeks of release when vulnerabilities are assessed as moderate or high by vendors.'),
(6, 'ml3', 3, 'Patches, updates or other vendor mitigations for vulnerabilities in operating systems of workstations, servers and network devices are applied within one month of release when vulnerabilities are assessed as low by vendors.'),
(6, 'ml3', 4, 'The latest release of operating systems is used.');

-- Control 7: Multi-factor Authentication (Mitigation Strategy 7)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(7, 'ml1', 1, 'Multi-factor authentication is used to authenticate users to their organisation''s internet-facing services.'),
(7, 'ml1', 2, 'Multi-factor authentication (where available) is used to authenticate users to third-party internet-facing services that process, store or communicate their organisation''s sensitive data.'),
(7, 'ml1', 3, 'Multi-factor authentication is used to authenticate privileged users to data repositories.'),
-- ML2 Requirements
(7, 'ml2', 1, 'Multi-factor authentication is used to authenticate users to their organisation''s internet-facing services.'),
(7, 'ml2', 2, 'Multi-factor authentication (where available) is used to authenticate users to third-party internet-facing services that process, store or communicate their organisation''s sensitive data.'),
(7, 'ml2', 3, 'Multi-factor authentication is used to authenticate privileged users to data repositories.'),
(7, 'ml2', 4, 'Multi-factor authentication is used to authenticate unprivileged users to data repositories.'),
(7, 'ml2', 5, 'Multi-factor authentication is used by an organisation''s users if they authenticate to their organisation''s systems via an internet-facing service.'),
-- ML3 Requirements
(7, 'ml3', 1, 'Multi-factor authentication is used to authenticate users to their organisation''s internet-facing services.'),
(7, 'ml3', 2, 'Multi-factor authentication (where available) is used to authenticate users to third-party internet-facing services that process, store or communicate their organisation''s sensitive data.'),
(7, 'ml3', 3, 'Multi-factor authentication is used to authenticate privileged users of systems, applications and data repositories.'),
(7, 'ml3', 4, 'Multi-factor authentication is used to authenticate unprivileged users of systems, applications and data repositories.'),
(7, 'ml3', 5, 'Multi-factor authentication is used by an organisation''s users if they authenticate to their organisation''s systems via an internet-facing service.'),
(7, 'ml3', 6, 'Multi-factor authentication uses either: something users have and something users know, or something users have that is unlocked by something users know or are.');

-- Control 8: Regular Backups (Mitigation Strategy 8)

INSERT INTO essential8_requirements (control_id, maturity_level, requirement_order, description) VALUES
-- ML1 Requirements
(8, 'ml1', 1, 'Backups of important data, software and configuration settings are performed at least daily.'),
(8, 'ml1', 2, 'Backups are synchronised to enable restoration to a common point in time.'),
(8, 'ml1', 3, 'Backups are retained for at least three months.'),
(8, 'ml1', 4, 'Restoration of backups to a common point in time is tested as part of disaster recovery exercises.'),
-- ML2 Requirements
(8, 'ml2', 1, 'Backups of important data, software and configuration settings are performed at least daily.'),
(8, 'ml2', 2, 'Backups are synchronised to enable restoration to a common point in time.'),
(8, 'ml2', 3, 'Backups are retained for at least three months.'),
(8, 'ml2', 4, 'Unprivileged accounts cannot access backups belonging to other accounts.'),
(8, 'ml2', 5, 'Unprivileged accounts are prevented from modifying and deleting backups.'),
(8, 'ml2', 6, 'Restoration of backups to a common point in time is tested as part of disaster recovery exercises at least once when initially implemented and each time fundamental information technology infrastructure changes occur.'),
-- ML3 Requirements
(8, 'ml3', 1, 'Backups of important data, software and configuration settings are performed at least daily.'),
(8, 'ml3', 2, 'Backups are synchronised to enable restoration to a common point in time.'),
(8, 'ml3', 3, 'Backups are retained for at least three months.'),
(8, 'ml3', 4, 'Unprivileged accounts (including backup administrator accounts) cannot access backups belonging to other accounts.'),
(8, 'ml3', 5, 'Unprivileged accounts (including backup administrator accounts) are prevented from modifying and deleting backups.'),
(8, 'ml3', 6, 'Privileged accounts (excluding backup administrator accounts) are prevented from modifying and deleting backups during their retention period.'),
(8, 'ml3', 7, 'Restoration of backups to a common point in time is tested as part of disaster recovery exercises at least once when initially implemented and each time fundamental information technology infrastructure changes occur.');
