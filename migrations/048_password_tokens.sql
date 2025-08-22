CREATE TABLE password_tokens (
  token VARCHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  expires_at DATETIME NOT NULL,
  used TINYINT(1) NOT NULL DEFAULT 0,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

UPDATE email_templates
SET body = '<p>Hello,</p><p>You have been invited to join {{companyName}}\'s portal.</p><p>To set your password, click <a href="{{setupLink}}">this link</a>.</p><p>Once set, login at <a href="{{portalUrl}}">{{portalUrl}}</a>.</p><img src="{{loginLogo}}" alt="{{companyName}} logo" />'
WHERE name = 'staff_invitation';
