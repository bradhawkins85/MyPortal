CREATE TABLE IF NOT EXISTS user_totp_authenticators (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  secret VARCHAR(255) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO user_totp_authenticators (user_id, name, secret)
SELECT id, 'Authenticator', totp_secret FROM users WHERE totp_secret IS NOT NULL;

ALTER TABLE users DROP COLUMN totp_secret;
