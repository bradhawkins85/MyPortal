CREATE TABLE roles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  description TEXT NULL,
  permissions JSON NULL,
  is_system TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO roles (name, description, permissions, is_system)
VALUES
  (
    'Owner',
    'Full control over company configuration and billing.',
    JSON_ARRAY('company.manage', 'membership.manage', 'billing.manage', 'audit.view'),
    1
  ),
  (
    'Administrator',
    'Manage memberships, roles, and operational settings.',
    JSON_ARRAY('membership.manage', 'audit.view'),
    1
  ),
  (
    'Member',
    'Standard access for day-to-day work.',
    JSON_ARRAY('portal.access'),
    1
  );

CREATE TABLE company_memberships (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  user_id INT NOT NULL,
  role_id INT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  invited_by INT NULL,
  invited_at DATETIME NULL,
  joined_at DATETIME NULL,
  last_seen_at DATETIME NULL,
  UNIQUE KEY uq_company_memberships_company_user (company_id, user_id),
  CONSTRAINT fk_company_memberships_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
  CONSTRAINT fk_company_memberships_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_company_memberships_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT,
  CONSTRAINT fk_company_memberships_invited_by FOREIGN KEY (invited_by) REFERENCES users(id) ON DELETE SET NULL
);
