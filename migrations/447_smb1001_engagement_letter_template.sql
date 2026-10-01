-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Editable letter of engagement that the SMB1001 compliance page can email to
-- ad hoc (non-VIP) customers to substantiate control TM-01.
INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'smb1001-engagement-letter-subject',
    'SMB1001 letter of engagement subject',
    'Subject line for the SMB1001 TM-01 letter of engagement. Variables: {{ company.name }}, {{ letter.date }}.',
    'text/plain',
    'Letter of engagement for IT support - {{ company.name }}'
  ),
  (
    'smb1001-engagement-letter',
    'SMB1001 letter of engagement',
    'Emailed from the SMB1001 compliance page to ad hoc customers to evidence control TM-01 (engaged IT support). Variables: {{ recipient.name }}, {{ recipient.email }}, {{ company.name }}, {{ sender.name }}, {{ sender.email }}, {{ letter.date }}.',
    'text/html',
    '<p>Dear {{ recipient.name }},</p><p>This letter confirms that {{ company.name }} engages Hawkins IT Solutions to provide IT support on an ad hoc basis, as of {{ letter.date }}.</p><p>Under this engagement, Hawkins IT Solutions will, when requested:</p><ul><li>support, maintain and secure your computers, servers, network and cloud services;</li><li>respond to support requests raised through the portal, by email or by phone;</li><li>advise on security improvements, including the SMB1001 cyber security controls.</li></ul><p>Work is carried out on request and charged at our standard rates unless a separate agreement says otherwise. Your staff can reach IT support through the MyPortal customer portal.</p><p>Please keep this letter with your compliance records. If you would like to discuss a managed service agreement, reply to this email.</p><p>Kind regards,<br>{{ sender.name }}<br>Hawkins IT Solutions</p>'
  );
