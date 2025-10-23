INSERT INTO knowledge_base_articles (slug, title, summary, content, permission_scope, is_published, published_at)
SELECT
    'admin-variables-lab',
    'Admin Variables Lab',
    'How to inspect automation payloads, capture context snapshots, and manage variable security.',
    CONCAT_WS('\n',
        '<section class="kb-article__section" data-section-index="1"><h2>Why the Variables Lab exists</h2><p>The Variables Lab captures the raw context emitted by automations, webhooks, alerts, and schedulers so administrators can verify which fields are available before deploying templates or filters. Use it to validate assumptions about payload structure, discover newly introduced keys, and confirm that sensitive values are being redacted upstream.</p><ul><li>The feed ingests events from production as well as the development installer when both environments share telemetry.</li><li>Snapshots store the original UTC timestamps and preserve the JSON envelope for later review.</li><li>Search reduces incident response times by allowing you to jump directly to the emitting integration or workflow run.</li></ul></section>',
        '<section class="kb-article__section" data-section-index="2"><h2>Access, retention, and prerequisites</h2><p>Only super administrators can access the Variables Lab because it may expose customer metadata. The module becomes active once at least one automation, webhook, or alert has been executed.</p><ol><li>Navigate to <strong>Admin ▸ Variables Lab</strong> from the left navigation pane.</li><li>Ensure the development installer is forwarding telemetry if you want to validate changes outside production. Use separate API credentials so the sandbox does not interact with production tenants.</li><li>Retention defaults to 30 days. Update the installer configuration if you need a different window and redeploy the service from the console or web UI.</li></ol><p>Historical snapshots are removed automatically by the nightly maintenance job that ships with the systemd service.</p></section>',
        '<section class="kb-article__section" data-section-index="3"><h2>Exploring payloads</h2><p>The lab UI follows the three-panel layout enforced across administrative apps. Use the left rail to filter by source module, the header controls to change the time range, and the main body to inspect payload details.</p><ul><li>Select a row to reveal the formatted JSON viewer. Keys are sorted alphabetically and the viewer highlights the path of the field you click.</li><li>Toggle the <em>Show UTC</em> switch to compare stored timestamps with your local timezone renderings. All data is persisted in UTC.</li><li>Use the <em>Copy path</em> action to place the dot-notation token on your clipboard for use in templates, filters, or the HTTP module.</li><li>When payloads include array data, the viewer surfaces indexes so you can reference values like <code>alert.labels.0</code>.</li></ul></section>',
        '<section class="kb-article__section" data-section-index="4"><h2>Designing automations with snapshots</h2><p>Snapshots remove guesswork when building filters and templates.</p><ol><li>Open a snapshot representing the workflow you are designing.</li><li>Use <em>Copy as cURL</em> to export the JSON payload for local testing with regression suites.</li><li>Highlight critical variables and add them to your automation documentation so the change log entry meets audit expectations.</li><li>Validate dynamic paths in the development installer, capture screenshots, and attach them to your change record.</li></ol><p>When integrating with external systems, confirm the webhook monitor shows successful delivery after deploying updates.</p></section>',
        '<section class="kb-article__section" data-section-index="5"><h2>Operational safeguards</h2><ul><li>Do not store secrets in payloads. Scrub or hash confidential values before they reach the Variables Lab.</li><li>Review webhook retry metrics in <strong>Admin ▸ Webhook Monitor</strong> after changes to upstream systems.</li><li>Grant access on a need-to-know basis. Use company-scoped knowledge base articles for tenant administrators who require reduced visibility.</li><li>Document every significant adjustment in the <code>changes</code> directory so migrations and automated imports remain accurate.</li><li>Run the installation or update scripts after modifying retention or service parameters to ensure systemd timers are refreshed.</li></ul></section>'
    ),
    'super_admin',
    1,
    '2025-12-08 10:00:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'admin-variables-lab'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'Why the Variables Lab exists',
       CONCAT_WS('\n',
           '<p>The Variables Lab records raw payloads emitted by automations, alerts, schedulers, and webhooks so you can inspect the available tokens before writing templates.</p>',
           '<ul>',
           '  <li>Use it to prove which keys exist and whether sensitive data is redacted.</li>',
           '  <li>Snapshots retain original UTC timestamps and payload envelopes for audit purposes.</li>',
           '  <li>Search by integration or workflow to reduce triage time during incidents.</li>',
           '</ul>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'admin-variables-lab'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'Access, retention, and prerequisites',
       CONCAT_WS('\n',
           '<p>Access is limited to super administrators because payloads may contain customer metadata. The module activates once any event source emits a payload.</p>',
           '<ol>',
           '  <li>Navigate to <strong>Admin ▸ Variables Lab</strong> from the left menu.</li>',
           '  <li>Connect the development installer if you want a safe space to validate upcoming automations.</li>',
           '  <li>Adjust the installer configuration if the default 30-day retention does not meet policy requirements.</li>',
           '</ol>',
           '<p>Nightly maintenance tasks purge expired snapshots after the installation scripts refresh the systemd timers.</p>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'admin-variables-lab'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'Exploring payloads',
       CONCAT_WS('\n',
           '<p>The Variables Lab UI follows the standard three-panel layout.</p>',
           '<ul>',
           '  <li>The left rail filters by module, integration, and workflow.</li>',
           '  <li>The header controls adjust the time range and toggle UTC vs. local time.</li>',
           '  <li>The main body renders the JSON viewer and highlights selected paths.</li>',
           '  <li>Use the copy actions to capture dot-notation keys such as <code>alert.labels.0</code>.</li>',
           '</ul>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'admin-variables-lab'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Designing automations with snapshots',
       CONCAT_WS('\n',
           '<p>Snapshots eliminate guesswork while constructing filters and templates.</p>',
           '<ol>',
           '  <li>Open a snapshot that matches the scenario you are designing.</li>',
           '  <li>Export the JSON via <em>Copy as cURL</em> for local regression tests.</li>',
           '  <li>List the required variables in your automation documentation and change log.</li>',
           '  <li>Validate updates inside the development installer and attach screenshots to the audit trail.</li>',
           '</ol>',
           '<p>After deployment, confirm the webhook monitor shows successful delivery.</p>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'admin-variables-lab'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 4
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 5, 'Operational safeguards',
       CONCAT_WS('\n',
           '<ul>',
           '  <li>Scrub secrets before payloads reach the Variables Lab.</li>',
           '  <li>Monitor webhook retries after upstream adjustments.</li>',
           '  <li>Restrict access to team members with an operational need.</li>',
           '  <li>Record every change in the <code>changes</code> directory for automated imports.</li>',
           '  <li>Run the install or update scripts when retention or service parameters change.</li>',
           '</ul>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'admin-variables-lab'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 5
  );
