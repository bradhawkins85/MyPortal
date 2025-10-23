INSERT INTO knowledge_base_articles (slug, title, summary, content, permission_scope, is_published, published_at)
SELECT
    'automation-trigger-filters-reference',
    'Automation Trigger Filters (JSON) Reference',
    'Comprehensive guide to building event trigger filters with 20 JSON samples and security guidance.',
    CONCAT_WS('\n',
        '<section class="kb-article__section" data-section-index="1"><h2>How trigger filters are evaluated</h2><p>Trigger filters are JSON objects that run against the event context provided when an automation is invoked. Evaluation is deterministic and short-circuits once a condition fails.</p><ul><li><code>any</code> passes when at least one child filter matches.</li><li><code>all</code> requires every child filter to match.</li><li><code>not</code> negates the result of the nested filter.</li><li><code>match</code> (implicit when no keyword is present) compares key/value pairs against the event context.</li><li>When no filter is supplied, the automation runs for every event.</li></ul></section>',
        '<section class="kb-article__section" data-section-index="2"><h2>JSON schema overview</h2><p>The filter document accepts a small vocabulary of keys. Combine them to model complex boolean logic without writing Python code.</p><table><thead><tr><th>Key</th><th>Value type</th><th>Purpose</th></tr></thead><tbody><tr><td><code>match</code></td><td>object</td><td>Dictionary of dot-separated paths mapped to expected values.</td></tr><tr><td><code>any</code></td><td>array</td><td>Evaluates child filters until one succeeds.</td></tr><tr><td><code>all</code></td><td>array</td><td>Every child filter must succeed.</td></tr><tr><td><code>not</code></td><td>object</td><td>Inverts the result of the nested filter.</td></tr><tr><td>(implicit)</td><td>object</td><td>If none of the reserved keys are present, the object is treated as a <code>match</code> block.</td></tr><tr><td>arrays</td><td>list</td><td>Lists supplied as values represent acceptable options; comparison succeeds when the actual value equals any entry.</td></tr></tbody></table><p>Only equality comparisons are supported. Compute thresholds or ranges upstream and expose them as booleans or enums in the event payload.</p></section>',
        '<section class="kb-article__section" data-section-index="3"><h2>Context paths and variables</h2><p>Paths use dot notation to traverse nested dictionaries. When the current value is a list, provide a numeric segment (for example <code>ticket.tags.0</code>) to inspect a specific index.</p><ul><li><strong>Ticket events:</strong> Access fields such as <code>ticket.status</code>, <code>ticket.priority</code>, <code>ticket.queue</code>, <code>ticket.company.id</code>, and <code>ticket.custom_fields.environment</code>.</li><li><strong>Alert payloads:</strong> Use keys like <code>alert.metrics.cpu.usage</code>, <code>alert.device.serial</code>, or <code>alert.labels.0</code>.</li><li><strong>Webhook inputs:</strong> Inspect <code>webhook.event</code>, <code>webhook.payload.site</code>, and <code>webhook.headers.X-Request-Id</code>.</li><li><strong>Scheduler context:</strong> Scheduled jobs can emit <code>schedule.window.start</code>, <code>schedule.window.end</code>, and <code>schedule.metadata.task</code>.</li><li><strong>System variables:</strong> Critical metadata such as <code>context.company.plan</code>, <code>context.environment</code>, or <code>context.user.email</code> are available when the emitting module supplies them. Always inspect the payload in Variables Lab before finalising production filters.</li></ul><p>Filters run before any template interpolation, so literal strings like <code>{{ COMPANY_NAME }}</code> are compared verbatim. Prefer matching on the raw context fields instead of rendered templates.</p></section>',
        '<section class="kb-article__section" data-section-index="4"><h2>Sample filter library</h2><p>The examples below cover common automation scenarios. Adapt the field names to match the structure visible in Variables Lab.</p><ol>
          <li><p><strong>Goal:</strong> Run for newly opened tickets.</p><pre>{\n  "match": {\n    "ticket.status": "open"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Accept multiple progressive states.</p><pre>{\n  "match": {\n    "ticket.status": ["open", "pending", "waiting_for_reply"]\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Limit to P1 service desk requests.</p><pre>{\n  "match": {\n    "ticket.priority": "p1",\n    "ticket.queue": "Service Desk"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Fire when escalation is true but the queue is not Network.</p><pre>{\n  "all": [\n    {\n      "match": {\n        "ticket.is_escalated": true\n      }\n    },\n    {\n      "not": {\n        "match": {\n          "ticket.queue": "Network"\n        }\n      }\n    }\n  ]\n}</pre></li>
          <li><p><strong>Goal:</strong> Restrict to a specific customer tenant.</p><pre>{\n  "match": {\n    "ticket.company.id": 1042\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Route tickets raised by the finance requester.</p><pre>{\n  "match": {\n    "ticket.requester.email": "finance@example.com"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Detect VIP-tagged cases (first tag slot).</p><pre>{\n  "match": {\n    "ticket.tags.0": "vip"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Target production environment incidents.</p><pre>{\n  "match": {\n    "ticket.custom_fields.environment": "production"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Trigger for managed tiers Gold or Platinum.</p><pre>{\n  "match": {\n    "ticket.company.service_tier": ["gold", "platinum"]\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Execute when breach countdown is enabled and minutes are low.</p><pre>{\n  "match": {\n    "ticket.sla.breach_imminent": true,\n    "ticket.sla.remaining_minutes": 15\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Fire after-hours requests from the on-call queue.</p><pre>{\n  "all": [\n    {\n      "match": {\n        "ticket.queue": "On Call"\n      }\n    },\n    {\n      "match": {\n        "event.triggered_at.hour": [0, 1, 2, 3, 4, 5, 22, 23]\n      }\n    }\n  ]\n}</pre></li>
          <li><p><strong>Goal:</strong> React to webhook device online notifications.</p><pre>{\n  "match": {\n    "webhook.event": "device.online"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Only when the payload site equals LON-01.</p><pre>{\n  "match": {\n    "webhook.payload.site": "LON-01"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Accept Syncro automation runs for billing profile A or B.</p><pre>{\n  "match": {\n    "context.integration": "syncro",\n    "context.billing_profile": ["profile-a", "profile-b"]\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> React when CPU usage breaches the 90 percent mark.</p><pre>{\n  "match": {\n    "alert.metrics.cpu.usage_percent": 90,\n    "alert.metrics.cpu.threshold_breached": true\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Match the first inventory change when an asset was removed.</p><pre>{\n  "match": {\n    "inventory.changes.0.action": "removed"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Filter to schedules dispatched by the nightly maintenance job.</p><pre>{\n  "match": {\n    "schedule.metadata.task": "nightly_maintenance"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Ensure the triggering user belongs to Operations.</p><pre>{\n  "match": {\n    "context.user.department": "Operations"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Only run when the last automation execution failed.</p><pre>{\n  "match": {\n    "automation.previous_run.status": "failed"\n  }\n}</pre></li>
          <li><p><strong>Goal:</strong> Combine multiple signals with fallbacks.</p><pre>{\n  "any": [\n    {\n      "all": [\n        {"match": {"ticket.status": "open"}},\n        {"match": {"ticket.queue": "Security"}}\n      ]\n    },\n    {\n      "all": [\n        {"match": {"alert.severity": "critical"}},\n        {"match": {"context.company.plan": "enterprise"}}\n      ]\n    }\n  ]\n}</pre></li>
          <li><p><strong>Goal:</strong> Block noisy lab tenants while allowing everyone else.</p><pre>{\n  "not": {\n    "match": {\n      "ticket.company.slug": ["lab-01", "lab-02"]\n    }\n  }\n}</pre></li>
        </ol></section>',
        '<section class="kb-article__section" data-section-index="5"><h2>Operational guidance and security</h2><ul><li>Validate every filter in the development installer before promoting to production. Capture screenshots for change control when UI adjustments are made.</li><li>Document approved filters in the associated change request so auditors can reconcile automation behaviour.</li><li>Prefer numeric identifiers over human-friendly names when available to avoid breakage during renames.</li><li>Keep payloads free from secrets—filters are stored in the database and rendered in the admin UI.</li><li>Ensure webhook retries remain monitored in the <strong>Admin ▸ Webhook Monitor</strong> after deploying new filters.</li></ul><p>Remember that migrations apply during startup. If you are adding new context fields, update the migration runner and regression tests accordingly so the JSON examples stay accurate.</p></section>'
    ),
    'super_admin',
    1,
    '2025-12-08 09:10:00'
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_base_articles WHERE slug = 'automation-trigger-filters-reference'
);

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 1, 'How trigger filters are evaluated',
       CONCAT_WS('\n',
           '<p>Trigger filters evaluate the JSON payload delivered with an event automation.</p>',
           '<ul>',
           '  <li><code>any</code> succeeds when at least one child filter matches.</li>',
           '  <li><code>all</code> requires every child to match; evaluation stops at the first failure.</li>',
           '  <li><code>not</code> negates the result of the nested filter.</li>',
           '  <li><code>match</code> (implicit when no keyword is present) compares key/value pairs against the context.</li>',
           '  <li>An empty filter set executes the automation for every incoming event.</li>',
           '</ul>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'automation-trigger-filters-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 1
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 2, 'JSON schema overview',
       CONCAT_WS('\n',
           '<p>The trigger filter DSL is intentionally compact.</p>',
           '<table>',
           '  <thead><tr><th>Key</th><th>Type</th><th>Description</th></tr></thead>',
           '  <tbody>',
           '    <tr><td><code>match</code></td><td>object</td><td>Dot-separated paths mapped to expected values.</td></tr>',
           '    <tr><td><code>any</code></td><td>array</td><td>Logical OR across child filters.</td></tr>',
           '    <tr><td><code>all</code></td><td>array</td><td>Logical AND across child filters.</td></tr>',
           '    <tr><td><code>not</code></td><td>object</td><td>Logical negation of the nested filter.</td></tr>',
           '    <tr><td>(implicit)</td><td>object</td><td>Objects without reserved keys are treated as <code>match</code> blocks.</td></tr>',
           '    <tr><td>arrays</td><td>list</td><td>Lists supplied as values act as allow-lists of acceptable matches.</td></tr>',
           '  </tbody>',
           '</table>',
           '<p>Create derived booleans in upstream systems to express threshold comparisons or regular expressions.</p>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'automation-trigger-filters-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 2
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 3, 'Context paths and variables',
       CONCAT_WS('\n',
           '<p>Use dot notation to walk nested dictionaries and indexes to reach list elements.</p>',
           '<ul>',
           '  <li><strong>Tickets:</strong> <code>ticket.status</code>, <code>ticket.priority</code>, <code>ticket.company.id</code>, <code>ticket.custom_fields.environment</code>.</li>',
           '  <li><strong>Alerts:</strong> <code>alert.metrics.cpu.usage</code>, <code>alert.device.serial</code>, <code>alert.labels.0</code>.</li>',
           '  <li><strong>Webhooks:</strong> <code>webhook.event</code>, <code>webhook.payload.site</code>, <code>webhook.headers.X-Request-Id</code>.</li>',
           '  <li><strong>Schedules:</strong> <code>schedule.window.start</code>, <code>schedule.window.end</code>, <code>schedule.metadata.task</code>.</li>',
           '  <li><strong>Context:</strong> <code>context.company.plan</code>, <code>context.environment</code>, <code>context.user.email</code>.</li>',
           '</ul>',
           '<p>Template variables such as <code>{{ COMPANY_NAME }}</code> are not interpolated inside filters; compare against the raw context values instead.</p>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'automation-trigger-filters-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 3
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 4, 'Sample filter library',
       CONCAT_WS('\n',
           '<p>Copy these snippets into the trigger filters editor and adjust the field names to match your event payload.</p>',
           '<ol>',
           '  <li><p><strong>Newly opened tickets</strong></p><pre>{\n  "match": {\n    "ticket.status": "open"\n  }\n}</pre></li>',
           '  <li><p><strong>Progress or waiting states</strong></p><pre>{\n  "match": {\n    "ticket.status": ["open", "pending", "waiting_for_reply"]\n  }\n}</pre></li>',
           '  <li><p><strong>P1 service desk</strong></p><pre>{\n  "match": {\n    "ticket.priority": "p1",\n    "ticket.queue": "Service Desk"\n  }\n}</pre></li>',
           '  <li><p><strong>Escalated but not network</strong></p><pre>{\n  "all": [\n    {"match": {"ticket.is_escalated": true}},\n    {"not": {"match": {"ticket.queue": "Network"}}}\n  ]\n}</pre></li>',
           '  <li><p><strong>Specific tenant</strong></p><pre>{\n  "match": {\n    "ticket.company.id": 1042\n  }\n}</pre></li>',
           '  <li><p><strong>Finance requester</strong></p><pre>{\n  "match": {\n    "ticket.requester.email": "finance@example.com"\n  }\n}</pre></li>',
           '  <li><p><strong>VIP tag</strong></p><pre>{\n  "match": {\n    "ticket.tags.0": "vip"\n  }\n}</pre></li>',
           '  <li><p><strong>Production incidents</strong></p><pre>{\n  "match": {\n    "ticket.custom_fields.environment": "production"\n  }\n}</pre></li>',
           '  <li><p><strong>Gold or platinum tier</strong></p><pre>{\n  "match": {\n    "ticket.company.service_tier": ["gold", "platinum"]\n  }\n}</pre></li>',
           '  <li><p><strong>SLA breach imminent</strong></p><pre>{\n  "match": {\n    "ticket.sla.breach_imminent": true,\n    "ticket.sla.remaining_minutes": 15\n  }\n}</pre></li>',
           '  <li><p><strong>After-hours on-call</strong></p><pre>{\n  "all": [\n    {"match": {"ticket.queue": "On Call"}},\n    {"match": {"event.triggered_at.hour": [0, 1, 2, 3, 4, 5, 22, 23]}}\n  ]\n}</pre></li>',
           '  <li><p><strong>Device online webhook</strong></p><pre>{\n  "match": {\n    "webhook.event": "device.online"\n  }\n}</pre></li>',
           '  <li><p><strong>Site LON-01</strong></p><pre>{\n  "match": {\n    "webhook.payload.site": "LON-01"\n  }\n}</pre></li>',
           '  <li><p><strong>Syncro billing profile</strong></p><pre>{\n  "match": {\n    "context.integration": "syncro",\n    "context.billing_profile": ["profile-a", "profile-b"]\n  }\n}</pre></li>',
           '  <li><p><strong>CPU breach</strong></p><pre>{\n  "match": {\n    "alert.metrics.cpu.usage_percent": 90,\n    "alert.metrics.cpu.threshold_breached": true\n  }\n}</pre></li>',
           '  <li><p><strong>Asset removed</strong></p><pre>{\n  "match": {\n    "inventory.changes.0.action": "removed"\n  }\n}</pre></li>',
           '  <li><p><strong>Nightly maintenance task</strong></p><pre>{\n  "match": {\n    "schedule.metadata.task": "nightly_maintenance"\n  }\n}</pre></li>',
           '  <li><p><strong>Operations department</strong></p><pre>{\n  "match": {\n    "context.user.department": "Operations"\n  }\n}</pre></li>',
           '  <li><p><strong>Previous run failed</strong></p><pre>{\n  "match": {\n    "automation.previous_run.status": "failed"\n  }\n}</pre></li>',
           '  <li><p><strong>Security or critical enterprise</strong></p><pre>{\n  "any": [\n    {"all": [{"match": {"ticket.status": "open"}}, {"match": {"ticket.queue": "Security"}}]},\n    {"all": [{"match": {"alert.severity": "critical"}}, {"match": {"context.company.plan": "enterprise"}}]}\n  ]\n}</pre></li>',
           '  <li><p><strong>Exclude lab tenants</strong></p><pre>{\n  "not": {\n    "match": {\n      "ticket.company.slug": ["lab-01", "lab-02"]\n    }\n  }\n}</pre></li>',
           '</ol>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'automation-trigger-filters-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 4
  );

INSERT INTO knowledge_base_sections (article_id, position, heading, content)
SELECT a.id, 5, 'Operational guidance and security',
       CONCAT_WS('\n',
           '<ul>',
           '  <li>Exercise every filter in the development installer before shipping to production and record the validation in your change log.</li>',
           '  <li>Prefer immutable identifiers (IDs, slugs) instead of labels that may be renamed.</li>',
           '  <li>Keep trigger payloads free of secrets because JSON filters are stored in plain text.</li>',
           '  <li>Monitor the webhook retry dashboard after changes to ensure external calls succeed.</li>',
           '  <li>When adding new context fields, extend regression tests and migration runners so downstream automations remain stable.</li>',
           '</ul>',
           '<p>Migrations run automatically during application startup; include this file-driven update with your deployment plan.</p>'
       )
FROM knowledge_base_articles a
WHERE a.slug = 'automation-trigger-filters-reference'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_base_sections s WHERE s.article_id = a.id AND s.position = 5
  );
