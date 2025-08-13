CREATE TABLE user_companies (
  user_id INT NOT NULL,
  company_id INT NOT NULL,
  can_manage_licenses TINYINT(1) DEFAULT 0,
  PRIMARY KEY (user_id, company_id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (company_id) REFERENCES companies(id)
);

INSERT INTO user_companies (user_id, company_id, can_manage_licenses)
SELECT id, company_id, 1 FROM users;
