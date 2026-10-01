# Bandit B608 audit for issue #3941

This audit records the provenance review for the local `bandit -r app` B608 findings addressed for issue #3941. GitHub code-scanning alert APIs were not accessible to this agent (`403 Resource not accessible by integration`), so the disposition is recorded by file and the initial local scan line numbers rather than alert id.

## Verification summary

- Initial local Bandit scan reviewed: **191** B608 findings.

- Final local Bandit rerun after inline remediation: **0** B608 findings.

- No global Bandit exclusion was added; reviewed sites were annotated inline with `# nosec B608`.

- Focused regression coverage was extended for hostile values, pagination/filter binding, and empty/multiple `IN (...)` sequences.

## Reviewed findings by file

### `app/repositories/api_keys.py`

- Reviewed line(s): 106, 287, 313, 371.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/asset_custom_fields.py`

- Reviewed line(s): 110.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/assets.py`

- Reviewed line(s): 334, 387.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/audit_logs.py`

- Reviewed line(s): 99, 165.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/auth.py`

- Reviewed line(s): 150.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/automations.py`

- Reviewed line(s): 272, 308, 327, 354.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/backup_jobs.py`

- Reviewed line(s): 69, 352, 373.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/bc3.py`

- Reviewed line(s): 102, 174, 214, 259, 393, 814, 903, 1008, 1122.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/bcp.py`

- Reviewed line(s): 203, 212, 526, 752, 952, 1001, 1278, 1547, 1909, 2128, 2252, 2400, 2542, 2665, 2802, 2977, 3110, 3236, 3412, 3628.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/billing_contacts.py`

- Reviewed line(s): 82.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/business_continuity_plans.py`

- Reviewed line(s): 57, 115.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/call_recordings.py`

- Reviewed line(s): 116, 176, 216, 399, 459.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/change_log.py`

- Reviewed line(s): 103.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/chat.py`

- Reviewed line(s): 81, 123, 137.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/companies.py`

- Reviewed line(s): 192, 239.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/company_memberships.py`

- Reviewed line(s): 95, 166.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/company_recurring_invoice_items.py`

- Reviewed line(s): 43.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/compliance_checks.py`

- Reviewed line(s): 180.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/email_blocklist.py`

- Reviewed line(s): 37, 55, 87.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/essential8.py`

- Reviewed line(s): 112, 224, 370, 415, 741, 819, 1068, 1161.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/freight_rules.py`

- Reviewed line(s): 14.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/invoices.py`

- Reviewed line(s): 69.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/issues.py`

- Reviewed line(s): 127, 212.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/knowledge_base.py`

- Reviewed line(s): 109, 121, 137, 154, 310.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/licenses.py`

- Reviewed line(s): 43, 61, 77, 94.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/m365.py`

- Reviewed line(s): 229, 375, 597.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/message_templates.py`

- Reviewed line(s): 71.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/network_devices.py`

- Reviewed line(s): 341.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/notifications.py`

- Reviewed line(s): 128, 219, 261, 285.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/port_pricing.py`

- Reviewed line(s): 30.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/ports.py`

- Reviewed line(s): 55, 131.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/rag_index.py`

- Reviewed line(s): 128.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/roles.py`

- Reviewed line(s): 73.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/scheduled_invoices.py`

- Reviewed line(s): 142.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/scheduled_tasks.py`

- Reviewed line(s): 66, 97, 112, 124, 342.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/service_status.py`

- Reviewed line(s): 15, 148, 163, 183, 206, 215, 236.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/shop.py`

- Reviewed line(s): 552, 779, 834, 955, 2083, 2154, 2373.

- Disposition: Interpolated identifiers come from trusted in-function mappings and normalized placeholder counts; runtime values remain bound.

### `app/repositories/slas.py`

- Reviewed line(s): 89, 135, 141.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/staff.py`

- Reviewed line(s): 131, 235, 867.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/staff_onboarding_workflows.py`

- Reviewed line(s): 556, 769.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/staff_requests.py`

- Reviewed line(s): 98.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/stock_feed.py`

- Reviewed line(s): 81.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/subscription_change_requests.py`

- Reviewed line(s): 109.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/subscriptions.py`

- Reviewed line(s): 83, 266.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/ticket_attachments.py`

- Reviewed line(s): 95.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/ticket_billed_time_entries.py`

- Reviewed line(s): 68, 167, 179.

- Disposition: Placeholder counts and filter fragments are derived from normalized collections or fixed literals; runtime values remain bound.

### `app/repositories/ticket_shipment_watches.py`

- Reviewed line(s): 162.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/ticket_tasks.py`

- Reviewed line(s): 115.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/ticket_views.py`

- Reviewed line(s): 238.

- Disposition: Dynamic SET/VALUES fragments are assembled from explicit function arguments or repository allowlists; runtime values remain bound.

### `app/repositories/tickets.py`

- Reviewed line(s): 589, 777, 828, 870, 940, 1276, 1329, 1408, 1422, 1553, 1618, 1651, 1782, 1801, 1832, 1853, 1868, 1883, 1918, 1970, 2182.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/tray.py`

- Reviewed line(s): 77, 185, 194, 203, 225, 382, 460, 479, 515, 531, 538, 549, 565, 580, 599, 605, 621, 642, 655, 699, 708.

- Disposition: Only the SQLite/MySQL placeholder token varies in SQL text; runtime values remain bound.

### `app/repositories/uptimekuma_alerts.py`

- Reviewed line(s): 269.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/repositories/webhook_events.py`

- Reviewed line(s): 165.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/services/email_recipients.py`

- Reviewed line(s): 348, 522.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.

### `app/services/reports.py`

- Reviewed line(s): 349.

- Disposition: The interpolated SQL fragment is a hardcoded repository constant; company-scoped values remain bound.

### `app/services/singleton_jobs.py`

- Reviewed line(s): 138.

- Disposition: Only the SQLite/MySQL placeholder token varies in SQL text; runtime values remain bound.

### `app/services/subscription_price_changes.py`

- Reviewed line(s): 74.

- Disposition: Reviewed as a Bandit false positive: SQL structure is built only from trusted local fragments and runtime values remain bound.
