-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 462: Client onboarding invitation details and editable email.
--
-- client_onboardings gains the contact's name and an optional personal message
-- the technician adds when creating the link. Both are used in the invitation
-- email, which now comes from two editable message templates (subject and
-- body) shared by every onboarding link.

ALTER TABLE client_onboardings ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255) NULL;
ALTER TABLE client_onboardings ADD COLUMN IF NOT EXISTS invite_message TEXT NULL;

INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'client-onboarding-invitation-subject',
    'Client onboarding invitation subject',
    'Subject line of the email that sends a new client their onboarding link. Variables: {{ recipient.greeting_name }}, {{ contact.name }}, {{ company.name }}, {{ sender.name }}, {{ app.name }}.',
    'text/plain',
    'Welcome to {{ app.name }} - let''s get {{ recipient.greeting_name }} set up'
  ),
  (
    'client-onboarding-invitation',
    'Client onboarding invitation',
    'Emailed to a new client with their onboarding link. One template is used for every onboarding link. Variables: {{ recipient.greeting_name }}, {{ contact.name }}, {{ contact.first_name }}, {{ contact.email }}, {{ company.name }}, {{ onboarding.link }}, {{ onboarding.expires }}, {{ onboarding.expires_days }}, {{ onboarding.message }}, {{ sender.name }}, {{ sender.email }}, {{ app.name }}, {{ portal.url }}.',
    'text/html',
    '<p>Hi {{ recipient.greeting_name }},</p><p>Welcome to {{ app.name }}! To set up support for {{ company.name }}, please tell us about your business, your sites and who should receive invoices. It takes about five minutes.</p>{{ onboarding.message }}<p><a href="{{ onboarding.link }}">Complete your onboarding form</a></p><p>This link is unique to you and works until {{ onboarding.expires }}. It can only be submitted once.</p><p>Kind regards,<br>{{ sender.name }}<br>{{ app.name }}</p>'
  );
