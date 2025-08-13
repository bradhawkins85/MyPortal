CREATE TABLE api_keys (
  id INT AUTO_INCREMENT PRIMARY KEY,
  api_key VARCHAR(64) NOT NULL UNIQUE,
  description VARCHAR(255),
  expiry_date DATE
);
