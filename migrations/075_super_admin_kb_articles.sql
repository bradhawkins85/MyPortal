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
    CONCAT_WS('\n',
        '<section class="kb-article__section" data-section-index="1">',
        '  <h2>Syntax and evaluation rules</h2>',
        '  <p>System variables follow the <code>{{ VARIABLE_NAME }}</code> format. They are evaluated server-side before responses are sent to clients.</p>',
        '  <ul>',
        '    <li>Variable names are uppercase with underscores.</li>',
        '    <li>Nesting is not supported; use dedicated helper functions instead.</li>',
        '    <li>If a variable is undefined, the renderer leaves the token unchanged so you can quickly spot configuration gaps.</li>',
        '    <li>Boolean values render as <code>true</code> or <code>false</code>; lists and mappings are joined into comma-separated strings.</li>',
        '  </ul>',
        '</section>',
        '<section class="kb-article__section" data-section-index="2">',
        '  <h2>Application and platform variables</h2>',
        '  <p>The following tokens are available on every render once the portal settings load.</p>',
        '  <table>',
        '    <thead>',
        '      <tr><th>Variable</th><th>Description</th></tr>',
        '    </thead>',
        '    <tbody>',
        '      <tr><td><code>{{ APP_NAME }}</code></td><td>Portal display name from configuration.</td></tr>',
        '      <tr><td><code>{{ APP_ENVIRONMENT }}</code></td><td>Active deployment environment (for example, <code>production</code> or <code>staging</code>).</td></tr>',
        '      <tr><td><code>{{ APP_ENV }}</code></td><td>Alias for <code>{{ APP_ENVIRONMENT }}</code> maintained for backwards compatibility.</td></tr>',
        '      <tr><td><code>{{ APP_PORTAL_URL }}</code></td><td>Fully-qualified base URL configured for the portal.</td></tr>',
        '      <tr><td><code>{{ APP_PORTAL_ORIGIN }}</code></td><td>Scheme and host portion of the portal URL, suitable for CORS policies.</td></tr>',
        '      <tr><td><code>{{ APP_PORTAL_HOSTNAME }}</code></td><td>Hostname extracted from the portal URL.</td></tr>',
        '      <tr><td><code>{{ APP_PORTAL_CONFIGURED }}</code></td><td>Returns <code>true</code> when a portal URL is set.</td></tr>',
        '      <tr><td><code>{{ APP_DEFAULT_TIMEZONE }}</code></td><td>Configured display timezone for scheduling and UI defaults.</td></tr>',
        '      <tr><td><code>{{ APP_CRON_TIMEZONE }}</code></td><td>Timezone used for scheduler cron expressions.</td></tr>',
        '      <tr><td><code>{{ APP_ENABLE_CSRF }}</code></td><td>Indicates whether CSRF protection is enforced.</td></tr>',
        '      <tr><td><code>{{ APP_ENABLE_AUTO_REFRESH }}</code></td><td>Signals if websocket-driven auto refresh is active.</td></tr>',
        '      <tr><td><code>{{ APP_SWAGGER_UI_URL }}</code></td><td>Path to the interactive API documentation.</td></tr>',
        '      <tr><td><code>{{ APP_SESSION_COOKIE_NAME }}</code></td><td>Name of the authentication session cookie.</td></tr>',
        '      <tr><td><code>{{ APP_ALLOWED_ORIGINS }}</code></td><td>Comma-separated list of additional origins permitted for CORS.</td></tr>',
        '      <tr><td><code>{{ APP_ALLOWED_ORIGIN_COUNT }}</code></td><td>Number of configured allowed origins.</td></tr>',
        '      <tr><td><code>{{ APP_DATABASE_BACKEND }}</code></td><td>Resolved database backend (<code>mysql</code> or <code>sqlite</code> fallback).</td></tr>',
        '      <tr><td><code>{{ APP_REDIS_ENABLED }}</code></td><td>True when a Redis connection string is configured.</td></tr>',
        '      <tr><td><code>{{ APP_SMTP_ENABLED }}</code></td><td>True when outbound email is configured.</td></tr>',
        '      <tr><td><code>{{ APP_STOCK_FEED_ENABLED }}</code></td><td>True when the stock feed endpoint is configured.</td></tr>',
        '      <tr><td><code>{{ APP_SYNCRO_WEBHOOK_ENABLED }}</code></td><td>True when the Syncro webhook URL is configured.</td></tr>',
        '      <tr><td><code>{{ APP_VERIFY_WEBHOOK_ENABLED }}</code></td><td>True when the Verify webhook URL is configured.</td></tr>',
        '      <tr><td><code>{{ APP_LICENSES_WEBHOOK_ENABLED }}</code></td><td>True when the licenses webhook URL is configured.</td></tr>',
        '      <tr><td><code>{{ APP_SHOP_WEBHOOK_ENABLED }}</code></td><td>True when the shop webhook URL is configured.</td></tr>',
        '      <tr><td><code>{{ APP_SMS_ENDPOINT_CONFIGURED }}</code></td><td>True when an SMS delivery endpoint is configured.</td></tr>',
        '      <tr><td><code>{{ APP_OPNFORM_BASE_URL }}</code></td><td>Base URL used for OpnForm integrations.</td></tr>',
        '      <tr><td><code>{{ APP_FAIL2BAN_LOG_PATH }}</code></td><td>Filesystem path to the Fail2ban log monitored by the portal.</td></tr>',
        '      <tr><td><code>{{ APP_MIGRATION_LOCK_TIMEOUT }}</code></td><td>Seconds the migration runner waits before timing out.</td></tr>',
        '      <tr><td><code>{{ APP_THEME }}</code></td><td>Active theme name applied to the UI.</td></tr>',
        '      <tr><td><code>{{ APP_STATIC_PATH }}</code></td><td>Absolute path to the static asset directory.</td></tr>',
        '      <tr><td><code>{{ APP_TEMPLATE_PATH }}</code></td><td>Absolute path to the template directory.</td></tr>',
        '      <tr><td><code>{{ PYTHON_IMPLEMENTATION }}</code></td><td>Python implementation running the portal (for example, CPython).</td></tr>',
        '      <tr><td><code>{{ PYTHON_VERSION }}</code></td><td>Python version string.</td></tr>',
        '      <tr><td><code>{{ PYTHON_RUNTIME }}</code></td><td>Combined implementation and version string.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_HOSTNAME }}</code></td><td>Host name of the application server.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_FQDN }}</code></td><td>Fully qualified domain name of the server.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PLATFORM }}</code></td><td>Operating system family.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PLATFORM_RELEASE }}</code></td><td>Operating system release identifier.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PLATFORM_VERSION }}</code></td><td>Detailed operating system version.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_ARCHITECTURE }}</code></td><td>CPU architecture reported by the host.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PROCESSOR }}</code></td><td>Processor string reported by Python.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PATH_SEPARATOR }}</code></td><td>Path separator used by the host platform.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_LINE_SEPARATOR }}</code></td><td>Line separator sequence used by the host platform.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_CWD }}</code></td><td>Current working directory for the portal process.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_APP_ROOT }}</code></td><td>Filesystem path to the application root.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_ENVIRONMENT_VARIABLE_COUNT }}</code></td><td>Total number of environment variables detected at runtime.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_PYTHON_EXECUTABLE }}</code></td><td>Absolute path to the Python interpreter executing the portal.</td></tr>',
        '      <tr><td><code>{{ APP_VERSION }}</code></td><td>Application version string when <code>version.txt</code> is present.</td></tr>',
        '    </tbody>',
        '  </table>',
        '  <p><strong>Note:</strong> Boolean results render as lower-case <code>true</code> or <code>false</code> strings so they can be consumed by templating logic.</p>',
        '</section>',
        '<section class="kb-article__section" data-section-index="3">',
        '  <h2>Runtime and time-based variables</h2>',
        '  <p>Time tokens resolve dynamically when the template is rendered.</p>',
        '  <table>',
        '    <thead>',
        '      <tr><th>Variable</th><th>Description</th></tr>',
        '    </thead>',
        '    <tbody>',
        '      <tr><td><code>{{ NOW_UTC }}</code></td><td>Current timestamp in ISO 8601 UTC format.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIME_UTC }}</code></td><td>Alias for <code>{{ NOW_UTC }}</code>.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIME_UTC_HUMAN }}</code></td><td>UTC timestamp formatted as <code>YYYY-MM-DD HH:MM:SSZ</code>.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_UNIX_TIMESTAMP }}</code></td><td>Seconds since the Unix epoch.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_UNIX_TIMESTAMP_MS }}</code></td><td>Milliseconds since the Unix epoch.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_DATE_UTC }}</code></td><td>UTC calendar date.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_YEAR_UTC }}</code></td><td>UTC year component.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_MONTH_UTC }}</code></td><td>UTC month component.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_DAY_UTC }}</code></td><td>UTC day component.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_ISO_WEEK_UTC }}</code></td><td>ISO week number in UTC.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_DAY_OF_YEAR_UTC }}</code></td><td>Day number within the UTC year.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIME_LOCAL }}</code></td><td>Current timestamp in the server local timezone.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_DATE_LOCAL }}</code></td><td>Local calendar date.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIMEZONE_NAME }}</code></td><td>Server reported timezone name.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIMEZONE_OFFSET_MINUTES }}</code></td><td>Offset from UTC in minutes.</td></tr>',
        '      <tr><td><code>{{ SYSTEM_TIMEZONE_OFFSET_HOURS }}</code></td><td>Offset from UTC in hours (decimal).</td></tr>',
        '    </tbody>',
        '  </table>',
        '  <p>Display layers convert UTC timestamps to the viewer local timezone automatically.</p>',
        '</section>',
        '<section class="kb-article__section" data-section-index="4">',
        '  <h2>Context-sensitive expansions</h2>',
        '  <p>Modules and data payloads expand the variable catalogue further.</p>',
        '  <ul>',
        '    <li><strong>Tickets:</strong> Tokens derived from ticket payloads use the <code>{{ TICKET_* }}</code> prefix such as <code>{{ TICKET_ID }}</code>, <code>{{ TICKET_PRIORITY }}</code>, <code>{{ TICKET_SUBJECT }}</code>, and <code>{{ TICKET_SUMMARY }}</code>. Nested data flattens into uppercase keys joined by underscores.</li>',
        '    <li><strong>Notifications:</strong> Counts and timestamps are exposed through <code>{{ NOTIFICATION_COUNT }}</code> and <code>{{ LAST_NOTIFICATION_AT }}</code>.</li>',
        '    <li><strong>Knowledge Base:</strong> Article metadata resolves via <code>{{ ARTICLE_TITLE }}</code> and <code>{{ ARTICLE_URL }}</code>.</li>',
        '    <li><strong>Environment pass-through:</strong> Non-sensitive environment variables prefixed with <code>APP_</code> or named <code>ENVIRONMENT</code>, <code>PORTAL_URL</code>, <code>CRON_TIMEZONE</code>, <code>ENABLE_CSRF</code>, <code>ENABLE_AUTO_REFRESH</code>, <code>SWAGGER_UI_URL</code>, <code>OPNFORM_BASE_URL</code>, <code>FAIL2BAN_LOG_PATH</code>, <code>SYSTEMD_SERVICE_NAME</code>, <code>APP_RESTART_COMMAND</code>, <code>TZ</code>, <code>LANG</code>, and <code>LC_ALL</code> are also surfaced for templates.</li>',
        '  </ul>',
        '  <p>Use <strong>Admin ▸ Variables Lab</strong> to inspect module payloads and confirm the token names produced by each workflow.</p>',
        '  <p>Execute template updates in a development environment first, then document successful evaluations in the associated change request before promoting to production.</p>',
        '</section>'
    ),
    'super_admin',
    1,
    '2025-12-08 09:05:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'system-variables-reference'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'Syntax and evaluation rules',
       '<p>System variables follow the <code>{{ VARIABLE_NAME }}</code> format. They are evaluated server-side before responses are sent to clients.</p>\n<ul>\n  <li>Variable names are uppercase with underscores.</li>\n  <li>Nesting is not supported; use dedicated helper functions instead.</li>\n  <li>If a variable is undefined, the renderer leaves the token unchanged so you can quickly spot configuration gaps.</li>\n  <li>Boolean values render as <code>true</code> or <code>false</code>; lists and mappings are joined into comma-separated strings.</li>\n</ul>
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'Application and platform variables',
       '<p>The following tokens are available on every render once the portal settings load.</p>\n<table>\n  <thead>\n    <tr><th>Variable</th><th>Description</th></tr>\n  </thead>\n  <tbody>\n    <tr><td><code>{{ APP_NAME }}</code></td><td>Portal display name from configuration.</td></tr>\n    <tr><td><code>{{ APP_ENVIRONMENT }}</code></td><td>Active deployment environment (for example, <code>production</code> or <code>staging</code>).</td></tr>\n    <tr><td><code>{{ APP_ENV }}</code></td><td>Alias for <code>{{ APP_ENVIRONMENT }}</code> maintained for backwards compatibility.</td></tr>\n    <tr><td><code>{{ APP_PORTAL_URL }}</code></td><td>Fully-qualified base URL configured for the portal.</td></tr>\n    <tr><td><code>{{ APP_PORTAL_ORIGIN }}</code></td><td>Scheme and host portion of the portal URL, suitable for CORS policies.</td></tr>\n    <tr><td><code>{{ APP_PORTAL_HOSTNAME }}</code></td><td>Hostname extracted from the portal URL.</td></tr>\n    <tr><td><code>{{ APP_PORTAL_CONFIGURED }}</code></td><td>Returns <code>true</code> when a portal URL is set.</td></tr>\n    <tr><td><code>{{ APP_DEFAULT_TIMEZONE }}</code></td><td>Configured display timezone for scheduling and UI defaults.</td></tr>\n    <tr><td><code>{{ APP_CRON_TIMEZONE }}</code></td><td>Timezone used for scheduler cron expressions.</td></tr>\n    <tr><td><code>{{ APP_ENABLE_CSRF }}</code></td><td>Indicates whether CSRF protection is enforced.</td></tr>\n    <tr><td><code>{{ APP_ENABLE_AUTO_REFRESH }}</code></td><td>Signals if websocket-driven auto refresh is active.</td></tr>\n    <tr><td><code>{{ APP_SWAGGER_UI_URL }}</code></td><td>Path to the interactive API documentation.</td></tr>\n    <tr><td><code>{{ APP_SESSION_COOKIE_NAME }}</code></td><td>Name of the authentication session cookie.</td></tr>\n    <tr><td><code>{{ APP_ALLOWED_ORIGINS }}</code></td><td>Comma-separated list of additional origins permitted for CORS.</td></tr>\n    <tr><td><code>{{ APP_ALLOWED_ORIGIN_COUNT }}</code></td><td>Number of configured allowed origins.</td></tr>\n    <tr><td><code>{{ APP_DATABASE_BACKEND }}</code></td><td>Resolved database backend (<code>mysql</code> or <code>sqlite</code> fallback).</td></tr>\n    <tr><td><code>{{ APP_REDIS_ENABLED }}</code></td><td>True when a Redis connection string is configured.</td></tr>\n    <tr><td><code>{{ APP_SMTP_ENABLED }}</code></td><td>True when outbound email is configured.</td></tr>\n    <tr><td><code>{{ APP_STOCK_FEED_ENABLED }}</code></td><td>True when the stock feed endpoint is configured.</td></tr>\n    <tr><td><code>{{ APP_SYNCRO_WEBHOOK_ENABLED }}</code></td><td>True when the Syncro webhook URL is configured.</td></tr>\n    <tr><td><code>{{ APP_VERIFY_WEBHOOK_ENABLED }}</code></td><td>True when the Verify webhook URL is configured.</td></tr>\n    <tr><td><code>{{ APP_LICENSES_WEBHOOK_ENABLED }}</code></td><td>True when the licenses webhook URL is configured.</td></tr>\n    <tr><td><code>{{ APP_SHOP_WEBHOOK_ENABLED }}</code></td><td>True when the shop webhook URL is configured.</td></tr>\n    <tr><td><code>{{ APP_SMS_ENDPOINT_CONFIGURED }}</code></td><td>True when an SMS delivery endpoint is configured.</td></tr>\n    <tr><td><code>{{ APP_OPNFORM_BASE_URL }}</code></td><td>Base URL used for OpnForm integrations.</td></tr>\n    <tr><td><code>{{ APP_FAIL2BAN_LOG_PATH }}</code></td><td>Filesystem path to the Fail2ban log monitored by the portal.</td></tr>\n    <tr><td><code>{{ APP_MIGRATION_LOCK_TIMEOUT }}</code></td><td>Seconds the migration runner waits before timing out.</td></tr>\n    <tr><td><code>{{ APP_THEME }}</code></td><td>Active theme name applied to the UI.</td></tr>\n    <tr><td><code>{{ APP_STATIC_PATH }}</code></td><td>Absolute path to the static asset directory.</td></tr>\n    <tr><td><code>{{ APP_TEMPLATE_PATH }}</code></td><td>Absolute path to the template directory.</td></tr>\n    <tr><td><code>{{ PYTHON_IMPLEMENTATION }}</code></td><td>Python implementation running the portal (for example, CPython).</td></tr>\n    <tr><td><code>{{ PYTHON_VERSION }}</code></td><td>Python version string.</td></tr>\n    <tr><td><code>{{ PYTHON_RUNTIME }}</code></td><td>Combined implementation and version string.</td></tr>\n    <tr><td><code>{{ SYSTEM_HOSTNAME }}</code></td><td>Host name of the application server.</td></tr>\n    <tr><td><code>{{ SYSTEM_FQDN }}</code></td><td>Fully qualified domain name of the server.</td></tr>\n    <tr><td><code>{{ SYSTEM_PLATFORM }}</code></td><td>Operating system family.</td></tr>\n    <tr><td><code>{{ SYSTEM_PLATFORM_RELEASE }}</code></td><td>Operating system release identifier.</td></tr>\n    <tr><td><code>{{ SYSTEM_PLATFORM_VERSION }}</code></td><td>Detailed operating system version.</td></tr>\n    <tr><td><code>{{ SYSTEM_ARCHITECTURE }}</code></td><td>CPU architecture reported by the host.</td></tr>\n    <tr><td><code>{{ SYSTEM_PROCESSOR }}</code></td><td>Processor string reported by Python.</td></tr>\n    <tr><td><code>{{ SYSTEM_PATH_SEPARATOR }}</code></td><td>Path separator used by the host platform.</td></tr>\n    <tr><td><code>{{ SYSTEM_LINE_SEPARATOR }}</code></td><td>Line separator sequence used by the host platform.</td></tr>\n    <tr><td><code>{{ SYSTEM_CWD }}</code></td><td>Current working directory for the portal process.</td></tr>\n    <tr><td><code>{{ SYSTEM_APP_ROOT }}</code></td><td>Filesystem path to the application root.</td></tr>\n    <tr><td><code>{{ SYSTEM_ENVIRONMENT_VARIABLE_COUNT }}</code></td><td>Total number of environment variables detected at runtime.</td></tr>\n    <tr><td><code>{{ SYSTEM_PYTHON_EXECUTABLE }}</code></td><td>Absolute path to the Python interpreter executing the portal.</td></tr>\n    <tr><td><code>{{ APP_VERSION }}</code></td><td>Application version string when <code>version.txt</code> is present.</td></tr>\n  </tbody>\n</table>\n<p><strong>Note:</strong> Boolean results render as lower-case <code>true</code> or <code>false</code> strings so they can be consumed by templating logic.</p>
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'Runtime and time-based variables',
       '<p>Time tokens resolve dynamically when the template is rendered.</p>\n<table>\n  <thead>\n    <tr><th>Variable</th><th>Description</th></tr>\n  </thead>\n  <tbody>\n    <tr><td><code>{{ NOW_UTC }}</code></td><td>Current timestamp in ISO 8601 UTC format.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIME_UTC }}</code></td><td>Alias for <code>{{ NOW_UTC }}</code>.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIME_UTC_HUMAN }}</code></td><td>UTC timestamp formatted as <code>YYYY-MM-DD HH:MM:SSZ</code>.</td></tr>\n    <tr><td><code>{{ SYSTEM_UNIX_TIMESTAMP }}</code></td><td>Seconds since the Unix epoch.</td></tr>\n    <tr><td><code>{{ SYSTEM_UNIX_TIMESTAMP_MS }}</code></td><td>Milliseconds since the Unix epoch.</td></tr>\n    <tr><td><code>{{ SYSTEM_DATE_UTC }}</code></td><td>UTC calendar date.</td></tr>\n    <tr><td><code>{{ SYSTEM_YEAR_UTC }}</code></td><td>UTC year component.</td></tr>\n    <tr><td><code>{{ SYSTEM_MONTH_UTC }}</code></td><td>UTC month component.</td></tr>\n    <tr><td><code>{{ SYSTEM_DAY_UTC }}</code></td><td>UTC day component.</td></tr>\n    <tr><td><code>{{ SYSTEM_ISO_WEEK_UTC }}</code></td><td>ISO week number in UTC.</td></tr>\n    <tr><td><code>{{ SYSTEM_DAY_OF_YEAR_UTC }}</code></td><td>Day number within the UTC year.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIME_LOCAL }}</code></td><td>Current timestamp in the server local timezone.</td></tr>\n    <tr><td><code>{{ SYSTEM_DATE_LOCAL }}</code></td><td>Local calendar date.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIMEZONE_NAME }}</code></td><td>Server reported timezone name.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIMEZONE_OFFSET_MINUTES }}</code></td><td>Offset from UTC in minutes.</td></tr>\n    <tr><td><code>{{ SYSTEM_TIMEZONE_OFFSET_HOURS }}</code></td><td>Offset from UTC in hours (decimal).</td></tr>\n  </tbody>\n</table>\n<p>Display layers convert UTC timestamps to the viewer local timezone automatically.</p>
FROM knowledge_base_articles a
WHERE a.slug = 'system-variables-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Context-sensitive expansions',
       '<p>Modules and data payloads expand the variable catalogue further.</p>\n<ul>\n  <li><strong>Tickets:</strong> Tokens derived from ticket payloads use the <code>{{ TICKET_* }}</code> prefix such as <code>{{ TICKET_ID }}</code>, <code>{{ TICKET_PRIORITY }}</code>, <code>{{ TICKET_SUBJECT }}</code>, and <code>{{ TICKET_SUMMARY }}</code>. Nested data flattens into uppercase keys joined by underscores.</li>\n  <li><strong>Notifications:</strong> Counts and timestamps are exposed through <code>{{ NOTIFICATION_COUNT }}</code> and <code>{{ LAST_NOTIFICATION_AT }}</code>.</li>\n  <li><strong>Knowledge Base:</strong> Article metadata resolves via <code>{{ ARTICLE_TITLE }}</code> and <code>{{ ARTICLE_URL }}</code>.</li>\n  <li><strong>Environment pass-through:</strong> Non-sensitive environment variables prefixed with <code>APP_</code> or named <code>ENVIRONMENT</code>, <code>PORTAL_URL</code>, <code>CRON_TIMEZONE</code>, <code>ENABLE_CSRF</code>, <code>ENABLE_AUTO_REFRESH</code>, <code>SWAGGER_UI_URL</code>, <code>OPNFORM_BASE_URL</code>, <code>FAIL2BAN_LOG_PATH</code>, <code>SYSTEMD_SERVICE_NAME</code>, <code>APP_RESTART_COMMAND</code>, <code>TZ</code>, <code>LANG</code>, and <code>LC_ALL</code> are also surfaced for templates.</li>\n</ul>\n<p>Use <strong>Admin ▸ Variables Lab</strong> to inspect module payloads and confirm the token names produced by each workflow.</p>\n<p>Execute template updates in a development environment first, then document successful evaluations in the associated change request before promoting to production.</p>
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
