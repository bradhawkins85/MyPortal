-- Seed super-admin only knowledge base articles for platform operations and integrations

INSERT INTO knowledge_base_articles (slug, title, summary, content, permission_scope, is_published, published_at)
SELECT
    'super-admin-operations-guide',
    'Super Admin Operations Guide',
    'Step-by-step instructions for configuring and operating MyPortal as a super administrator.',
    '<section class="kb-article__section" data-section-index="1"><h2>Access prerequisites</h2><p>Only super administrators can view this runbook. Confirm that your profile is marked as super admin before opening the administration console.</p><ul><li>Navigate to <strong>Admin ▸ Users</strong> and verify the <em>Super Admin</em> badge on your account.</li><li>Ensure multi-factor authentication is enforced for all super admins.</li><li>Use a dedicated administrative browser profile to avoid cross-session data leakage.</li></ul></section><section class="kb-article__section" data-section-index="2"><h2>Initial environment configuration</h2><p>Review the <code>.env</code> file and the corresponding <code>.env.example</code> template before bootstrapping an environment.</p><ol><li>Set database credentials or allow the SQLite fallback by leaving MySQL variables blank.</li><li>Populate OAuth, SMTP, and webhook secrets as required; leave unused providers unset.</li><li>Store all times in UTC within the configuration; presentation layers automatically convert to the viewer''s locale.</li></ol><p>Update both production and development installer scripts so they fetch the latest environment variables.</p></section><section class="kb-article__section" data-section-index="3"><h2>Layout and branding controls</h2><p>MyPortal uses a three-panel layout: a left navigation rail, a right-hand contextual header, and the main content body. To update branding assets:</p><ul><li>Upload favicon and logo files through <strong>Admin ▸ Appearance</strong>.</li><li>Select the active theme or create a new one. Themes control typography, color tokens, and spacing scales.</li><li>Preview changes in the development sandbox before promoting them to production.</li></ul></section><section class="kb-article__section" data-section-index="4"><h2>Security and auditing</h2><p>Follow the security checklist after each release cycle.</p><ul><li>Rotate API keys and ensure the Swagger UI reflects any endpoint or schema updates.</li><li>Confirm webhook monitors are healthy and that retry policies are active.</li><li>Export audit trails for administrator activity and archive them in long-term storage.</li></ul></section><section class="kb-article__section" data-section-index="5"><h2>Release and maintenance workflow</h2><p>Every deployment must use the provided installation scripts.</p><ul><li>Run the production installer to pull from the private Git repository using credentials sourced from <code>.env</code>.</li><li>Confirm the systemd services restart cleanly and that migrations run automatically on startup.</li><li>Execute the development installer to validate changes against the staging database replica.</li><li>Record all updates in <code>changes.md</code> with timestamps in UTC and mark them as Fix or Feature.</li></ul></section>',
    'super_admin',
    1,
    '2025-12-08 09:00:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'super-admin-operations-guide'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'Access prerequisites',
       '<p>Only super administrators can view this runbook. Confirm that your profile is marked as super admin before opening the administration console.</p><ul><li>Navigate to <strong>Admin ▸ Users</strong> and verify the <em>Super Admin</em> badge on your account.</li><li>Ensure multi-factor authentication is enforced for all super admins.</li><li>Use a dedicated administrative browser profile to avoid cross-session data leakage.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'super-admin-operations-guide'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'Initial environment configuration',
       '<p>Review the <code>.env</code> file and the corresponding <code>.env.example</code> template before bootstrapping an environment.</p><ol><li>Set database credentials or allow the SQLite fallback by leaving MySQL variables blank.</li><li>Populate OAuth, SMTP, and webhook secrets as required; leave unused providers unset.</li><li>Store all times in UTC within the configuration; presentation layers automatically convert to the viewer''s locale.</li></ol><p>Update both production and development installer scripts so they fetch the latest environment variables.</p>'
FROM knowledge_base_articles a
WHERE a.slug = 'super-admin-operations-guide'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'Layout and branding controls',
       '<p>MyPortal uses a three-panel layout: a left navigation rail, a right-hand contextual header, and the main content body. To update branding assets:</p><ul><li>Upload favicon and logo files through <strong>Admin ▸ Appearance</strong>.</li><li>Select the active theme or create a new one. Themes control typography, color tokens, and spacing scales.</li><li>Preview changes in the development sandbox before promoting them to production.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'super-admin-operations-guide'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Security and auditing',
       '<p>Follow the security checklist after each release cycle.</p><ul><li>Rotate API keys and ensure the Swagger UI reflects any endpoint or schema updates.</li><li>Confirm webhook monitors are healthy and that retry policies are active.</li><li>Export audit trails for administrator activity and archive them in long-term storage.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'super-admin-operations-guide'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 4
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 5, 'Release and maintenance workflow',
       '<p>Every deployment must use the provided installation scripts.</p><ul><li>Run the production installer to pull from the private Git repository using credentials sourced from <code>.env</code>.</li><li>Confirm the systemd services restart cleanly and that migrations run automatically on startup.</li><li>Execute the development installer to validate changes against the staging database replica.</li><li>Record all updates in <code>changes.md</code> with timestamps in UTC and mark them as Fix or Feature.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'super-admin-operations-guide'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 5
  );

INSERT INTO knowledge_base_articles (slug, title, summary, content, permission_scope, is_published, published_at)
SELECT
    'system-variables-reference',
    'System Variables Reference',
    'Comprehensive catalogue of system variables available throughout MyPortal and how to apply them.',
    '<section class="kb-article__section" data-section-index="1"><h2>Syntax and evaluation rules</h2><p>System variables follow the <code>{{ VARIABLE_NAME }}</code> format. They are evaluated server-side before responses are sent to clients.</p><ul><li>Variable names are uppercase with underscores.</li><li>Nesting is not supported; use dedicated helper functions instead.</li><li>If a variable is undefined, the renderer leaves the token unchanged so you can quickly spot configuration gaps.</li></ul></section><section class="kb-article__section" data-section-index="2"><h2>Global context variables</h2><p>The following tokens are resolved for any authenticated request:</p><table><thead><tr><th>Variable</th><th>Description</th></tr></thead><tbody><tr><td><code>{{ USER_NAME }}</code></td><td>Displays the signed-in user''s display name.</td></tr><tr><td><code>{{ USER_EMAIL }}</code></td><td>Outputs the user''s primary email address.</td></tr><tr><td><code>{{ COMPANY_NAME }}</code></td><td>Shows the active company when the user belongs to multiple organisations.</td></tr><tr><td><code>{{ NOW_UTC }}</code></td><td>Renders the current timestamp in UTC; UI layers localise automatically.</td></tr></tbody></table></section><section class="kb-article__section" data-section-index="3"><h2>Module-specific variables</h2><p>Modules extend the variable namespace with scoped values.</p><ul><li><strong>Tickets:</strong> <code>{{ TICKET_ID }}</code>, <code>{{ TICKET_PRIORITY }}</code>, <code>{{ TICKET_SUMMARY }}</code></li><li><strong>Notifications:</strong> <code>{{ NOTIFICATION_COUNT }}</code>, <code>{{ LAST_NOTIFICATION_AT }}</code></li><li><strong>Knowledge Base:</strong> <code>{{ ARTICLE_TITLE }}</code>, <code>{{ ARTICLE_URL }}</code></li></ul><p>Consult Swagger documentation when new modules expose additional variables.</p></section><section class="kb-article__section" data-section-index="4"><h2>Validation and testing workflow</h2><p>Use the development installer to spin up a sandbox environment before promoting new templates.</p><ol><li>Open <strong>Admin ▸ Variables Lab</strong> to preview rendered content.</li><li>Leverage unit tests in <code>tests/</code> to cover regression cases for critical templates.</li><li>Document successful evaluations directly in the associated change request.</li></ol></section>',
    'super_admin',
    1,
    '2025-12-08 09:05:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'system-variables-reference'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'Syntax and evaluation rules',
       '<p>System variables follow the <code>{{ VARIABLE_NAME }}</code> format. They are evaluated server-side before responses are sent to clients.</p><ul><li>Variable names are uppercase with underscores.</li><li>Nesting is not supported; use dedicated helper functions instead.</li><li>If a variable is undefined, the renderer leaves the token unchanged so you can quickly spot configuration gaps.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'Global context variables',
       '<p>The following tokens are resolved for any authenticated request:</p><table><thead><tr><th>Variable</th><th>Description</th></tr></thead><tbody><tr><td><code>{{ USER_NAME }}</code></td><td>Displays the signed-in user''s display name.</td></tr><tr><td><code>{{ USER_EMAIL }}</code></td><td>Outputs the user''s primary email address.</td></tr><tr><td><code>{{ COMPANY_NAME }}</code></td><td>Shows the active company when the user belongs to multiple organisations.</td></tr><tr><td><code>{{ NOW_UTC }}</code></td><td>Renders the current timestamp in UTC; UI layers localise automatically.</td></tr></tbody></table>'
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'Module-specific variables',
       '<p>Modules extend the variable namespace with scoped values.</p><ul><li><strong>Tickets:</strong> <code>{{ TICKET_ID }}</code>, <code>{{ TICKET_PRIORITY }}</code>, <code>{{ TICKET_SUMMARY }}</code></li><li><strong>Notifications:</strong> <code>{{ NOTIFICATION_COUNT }}</code>, <code>{{ LAST_NOTIFICATION_AT }}</code></li><li><strong>Knowledge Base:</strong> <code>{{ ARTICLE_TITLE }}</code>, <code>{{ ARTICLE_URL }}</code></li></ul><p>Consult Swagger documentation when new modules expose additional variables.</p>'
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Validation and testing workflow',
       '<p>Use the development installer to spin up a sandbox environment before promoting new templates.</p><ol><li>Open <strong>Admin ▸ Variables Lab</strong> to preview rendered content.</li><li>Leverage unit tests in <code>tests/</code> to cover regression cases for critical templates.</li><li>Document successful evaluations directly in the associated change request.</li></ol>'
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 4
  );

INSERT INTO knowledge_base_articles (slug, title, summary, content, permission_scope, is_published, published_at)
SELECT
    'http-post-module-reference',
    'HTTP POST Module Reference',
    'Technical reference for configuring the HTTP POST automation module, including payload schemas and retry behaviour.',
    '<section class="kb-article__section" data-section-index="1"><h2>Module overview</h2><p>The HTTP POST module delivers outbound requests to third-party services with automatic retry handling and failure monitoring.</p><ul><li>Executions are logged and exposed in <strong>Admin ▸ Webhook Monitor</strong>.</li><li>Retries follow exponential backoff with jitter for transient errors.</li><li>Payloads can interpolate system variables before dispatch.</li></ul></section><section class="kb-article__section" data-section-index="2"><h2>Endpoint configuration</h2><p>Configure destinations from <strong>Admin ▸ Automations ▸ HTTP POST</strong>.</p><ol><li>Provide a descriptive name and the target URL (HTTPS strongly recommended).</li><li>Optionally add custom headers such as <code>Authorization</code> or <code>X-Trace-Id</code>.</li><li>Select the authentication profile if credential rotation is managed centrally.</li></ol></section><section class="kb-article__section" data-section-index="3"><h2>POST body format</h2><p>Requests default to <code>application/json</code>. The canonical payload structure is:</p><pre>{
  "event": "string",
  "occurred_at": "2025-12-08T09:10:00Z",
  "payload": {
    "summary": "string",
    "details": "string",
    "metadata": {
      "key": "value"
    }
  },
  "variables": {
    "{{ USER_EMAIL }}": "resolved@example.com"
  }
}</pre><p>Replace the sample values with context-specific data. Use system variables inside the <code>payload</code> object to inject runtime values.</p></section><section class="kb-article__section" data-section-index="4"><h2>Security considerations</h2><p>Always transmit over TLS 1.2 or higher.</p><ul><li>Enable HMAC signing when supported by the destination service.</li><li>Store secrets in the credential vault; never hard-code tokens.</li><li>Review webhook monitoring after deployments to confirm no retries remain pending.</li></ul></section><section class="kb-article__section" data-section-index="5"><h2>Testing and troubleshooting</h2><p>Validate integrations before enabling them in production.</p><ol><li>Use the development installer environment to execute dry-run POSTs.</li><li>Inspect request and response logs within the webhook monitor.</li><li>Capture failures in the change request along with remediation steps.</li></ol>',
    'super_admin',
    1,
    '2025-12-08 09:10:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'http-post-module-reference'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'Module overview',
       '<p>The HTTP POST module delivers outbound requests to third-party services with automatic retry handling and failure monitoring.</p><ul><li>Executions are logged and exposed in <strong>Admin ▸ Webhook Monitor</strong>.</li><li>Retries follow exponential backoff with jitter for transient errors.</li><li>Payloads can interpolate system variables before dispatch.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'http-post-module-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'Endpoint configuration',
       '<p>Configure destinations from <strong>Admin ▸ Automations ▸ HTTP POST</strong>.</p><ol><li>Provide a descriptive name and the target URL (HTTPS strongly recommended).</li><li>Optionally add custom headers such as <code>Authorization</code> or <code>X-Trace-Id</code>.</li><li>Select the authentication profile if credential rotation is managed centrally.</li></ol>'
FROM knowledge_base_articles a
WHERE a.slug = 'http-post-module-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'POST body format',
       '<p>Requests default to <code>application/json</code>. The canonical payload structure is:</p><pre>{
  "event": "string",
  "occurred_at": "2025-12-08T09:10:00Z",
  "payload": {
    "summary": "string",
    "details": "string",
    "metadata": {
      "key": "value"
    }
  },
  "variables": {
    "{{ USER_EMAIL }}": "resolved@example.com"
  }
}</pre><p>Replace the sample values with context-specific data. Use system variables inside the <code>payload</code> object to inject runtime values.</p>'
FROM knowledge_base_articles a
WHERE a.slug = 'http-post-module-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Security considerations',
       '<p>Always transmit over TLS 1.2 or higher.</p><ul><li>Enable HMAC signing when supported by the destination service.</li><li>Store secrets in the credential vault; never hard-code tokens.</li><li>Review webhook monitoring after deployments to confirm no retries remain pending.</li></ul>'
FROM knowledge_base_articles a
WHERE a.slug = 'http-post-module-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 4
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 5, 'Testing and troubleshooting',
       '<p>Validate integrations before enabling them in production.</p><ol><li>Use the development installer environment to execute dry-run POSTs.</li><li>Inspect request and response logs within the webhook monitor.</li><li>Capture failures in the change request along with remediation steps.</li></ol>'
FROM knowledge_base_articles a
WHERE a.slug = 'http-post-module-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 5
  );
