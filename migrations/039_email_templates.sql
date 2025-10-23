CREATE TABLE IF NOT EXISTS email_templates (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) UNIQUE NOT NULL,
  subject VARCHAR(255) NOT NULL,
  body TEXT NOT NULL
);

INSERT INTO email_templates (name, subject, body) VALUES (
  'staff_invitation',
  'You have been invited to {{companyName}}\'s portal',
  '<p>Hello,</p><p>You have been invited to join {{companyName}}\'s portal.</p><p>Your temporary password is: {{tempPassword}}</p><p>Please login at <a href="{{portalUrl}}">{{portalUrl}}</a> and change your password upon first login.</p><img src="{{loginLogo}}" alt="{{companyName}} logo" />'
);
