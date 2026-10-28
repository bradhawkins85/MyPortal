-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Editable welcome aboard email for new customers, with a separate subject
-- template. The body links to the company's tray agent deployment URL, which
-- {{ tray.deploymentUrl }} resolves for the company being emailed.
INSERT IGNORE INTO message_templates (slug, name, description, content_type, content) VALUES
  (
    'new-customer-welcome-email-subject',
    'New Customer Welcome Email subject',
    'Subject line of the welcome aboard email sent to new customers. Variables: {{ company.name }}.',
    'text/plain',
    'Welcome aboard, {{ company.name }} - getting started with Hawkins IT Solutions'
  ),
  (
    'new-customer-welcome-email',
    'New Customer Welcome Email',
    'Welcome aboard email sent to new customers. Explains the tray agent and links to the company''s tray agent installer. Variables: {{ company.name }}, {{ tray.deploymentUrl }}.',
    'text/html',
    '<p>Hi {{ company.name }} team,</p><p>Welcome aboard! We''re delighted to be looking after your IT and are looking forward to working with you.</p><p>To get started, please install our support agent on each computer you would like us to look after:</p><p><a href="{{ tray.deploymentUrl }}">Download the support agent installer</a></p><p>If the link does not open, copy this address into your web browser:<br>{{ tray.deploymentUrl }}</p><h3>What the support agent does</h3><p>The agent runs quietly in the background and adds an icon to your system tray (or menu bar on a Mac). It lets us:</p><ul><li><strong>Help you faster</strong> - chat with our support team and reach our support links straight from the tray icon.</li><li><strong>Provide remote support</strong> - connect to your computer to resolve issues without a site visit.</li><li><strong>Keep your computers healthy</strong> - monitor performance, temperatures and other health checks so we can fix problems before they affect you.</li><li><strong>Keep you secure</strong> - check the status of your antivirus protection and alert us to detected threats.</li><li><strong>Keep accurate records</strong> - record each computer''s hardware and installed software so we can plan upgrades, renewals and licensing.</li><li><strong>Perform maintenance</strong> - run approved maintenance tasks and send you notifications when needed.</li><li><strong>Stay up to date</strong> - the agent updates itself automatically, so there is nothing for you to maintain.</li></ul><h3>A note about antivirus software</h3><p>Some antivirus products may block or quarantine the agent during installation because it includes remote support and maintenance tools. If this happens, you may need to temporarily disable your antivirus while you install the agent, then turn it back on straight away once the installation finishes. If you are not comfortable doing this, or the install still does not complete, just reply to this email and we will help you.</p><p>If you have any questions at all, reply to this email or contact our support team - we''re happy to help.</p><p>Kind regards,<br>The Hawkins IT Solutions team</p>'
  );
