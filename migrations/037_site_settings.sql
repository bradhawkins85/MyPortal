CREATE TABLE site_settings (
  id INT PRIMARY KEY,
  company_name VARCHAR(255),
  login_logo LONGTEXT,
  sidebar_logo LONGTEXT
);

INSERT INTO site_settings (id) VALUES (1);
