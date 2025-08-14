CREATE TABLE office_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  company_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE office_group_members (
  group_id INT NOT NULL,
  staff_id INT NOT NULL,
  PRIMARY KEY (group_id, staff_id),
  FOREIGN KEY (group_id) REFERENCES office_groups(id) ON DELETE CASCADE,
  FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);
