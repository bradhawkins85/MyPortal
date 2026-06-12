ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS account_verification_tokens (
  token VARCHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  expires_at DATETIME NOT NULL,
  used TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'staff_invitation',
    'Staff invitation email',
    'Sent when a staff member is invited to MyPortal. Variables include {{ user.name }}, {{ user.email }}, {{ company.name }}, {{ invitation.link }}, {{ portal.login_url }}, and {{ app.name }}.',
    'text/html',
    '<p>Hello {{ user.name }},</p><p>You have been invited to access {{ app.name }} for {{ company.name }}.</p><p><a href="{{ invitation.link }}">Set your password and activate your account</a></p><p>This link expires in one hour. If you were not expecting this invitation, you can ignore this email.</p>'
  ),
  (
    'signup_verification',
    'Signup verification email',
    'Sent after signup so the user can verify their email before accessing MyPortal. Variables include {{ user.name }}, {{ user.email }}, {{ verification.link }}, {{ portal.login_url }}, and {{ app.name }}.',
    'text/html',
    '<p>Hello {{ user.name }},</p><p>Thanks for signing up for {{ app.name }}. Please verify your email address before signing in.</p><p><a href="{{ verification.link }}">Verify your signup</a></p><p>This link expires in 24 hours. If you did not create this account, you can ignore this email.</p>'
  );
