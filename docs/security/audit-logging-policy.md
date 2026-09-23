# Audit logging policy and coverage

**Owner:** Security and platform engineering  
**Applies to:** API and feature routes, services, webhooks, scheduled tasks,
background workers, and administrative workflows  
**Canonical implementation:** `app.services.audit`

This policy is the baseline for all audit-coverage work. Audit records describe
changes to business or security state; operational logs describe execution.
Do not use an audit row as a substitute for metrics, traces, or error logs.

## Event classification

### Mandatory

Record an event after a successful state transition for:

* create, update, archive, restore, and delete operations;
* permission, role, membership, tenant assignment, and configuration changes;
* login failures, credential/passkey/MFA changes, session revocation,
  impersonation, API-key lifecycle, and other security-sensitive access events;
* manual execution or retry of privileged, destructive, import, export,
  synchronisation, remediation, backup, or maintenance operations; and
* material automated changes made by webhooks, schedules, integrations, or
  background processes, including their material failed or blocked outcomes.

Authentication successes are mandatory when they establish unusual or
privileged access (passkey, impersonation, API key, recovery); routine session
refresh is excluded. Record denied privileged attempts when doing so is useful
for security monitoring, but never record the supplied credential.

### Optional

Record high-value read operations only when disclosure itself is sensitive
(for example a regulated export), and lifecycle milestones which help explain
a material state transition. Optional events must follow the same naming,
field, redaction, and failure-isolation requirements as mandatory events.

### Excluded by default

Do not audit routine page views, list/detail reads, searches, health checks,
polling, heartbeats, status reporting, previews, validation-only requests, or
successful background processing that changes no business/security state.
Do not emit an event for a no-op update. Operational failures with no state
change belong in application monitoring unless they are security-relevant or
a material automated outcome.

## Required event envelope

Every event has `action`, `entity_type`, and the best available stable
`entity_id`. Include `metadata.company_id` for tenant-scoped data and a concise
outcome when a before/after diff cannot express the result. The canonical API
automatically records changed fields, redacts sensitive values, and suppresses
no-op updates.

| Origin | Actor fields | Correlation fields | Required metadata |
| --- | --- | --- | --- |
| UI/user session | `user_id` | `request_id`, source IP | `source="ui"`; `company_id` when scoped |
| API key | key identity in `api_key`; `user_id` when delegated | `request_id`, source IP | `source="api_key"`; tenant and concise outcome |
| Webhook | `actor` = verified provider/integration | delivery/request ID and source IP when trustworthy | `source="webhook"`; tenant and provider event ID (not payload) |
| Scheduled task | `actor` = stable task name | run/job ID | `source="scheduled"`; tenant and concise outcome |
| Background process | `actor` = worker/integration name | job/request ID when inherited | `source="background"`; tenant and concise outcome |
| System-generated | `actor` = stable subsystem name | causal request/job ID when available | `source="system"`; tenant and reason/outcome |

For an unattended event, `user_id` may be null, but `actor` must identify the
system. Never invent a user. Source and actor are stored in redacted metadata.

## Action taxonomy

Use lowercase `<entity>.<verb>` or `<domain>.<entity>.<verb>`. The last segment
is an imperative, present-tense verb: `create`, `update`, `archive`, `restore`,
`delete`, `add`, `remove`, `assign`, `approve`, `deny`, `enable`, `disable`,
`execute`, `retry`, `rotate`, `revoke`, `start`, `complete`, `fail`, or another
precise domain verb. Qualifiers belong before the verb, for example
`auth.passkey.rename`, `ticket.watcher.add`, and `shop.product.archive`.

Do not introduce bare actions (`create`), past-tense variants (`created`,
`deleted`, `replied`), spaces, mixed case, or synonyms for the same transition.
Historical `log_action` call sites remain migration debt; all new integrations
must use `record`, `record_create`, or `record_delete`. The modern API validates
action format at runtime.

## Data minimisation and failure behaviour

Never store passwords, credentials, secrets, tokens, authorization/cookie
headers, private keys, raw webhook payloads, email/chat/ticket message bodies,
or entire request/response bodies. Pass domain-specific field names through
`sensitive_extra_keys`. Prefer identifiers, counts, status, and bounded error
categories. The canonical implementation truncates large scalar values, but
truncation is not permission to submit a message body.

Audit persistence **fails open**: a database failure must not roll back or
fail the business operation. `audit.db_write_failed` must remain visible in
application monitoring and must include the action and entity identifiers,
without the sensitive event content.

## High-risk coverage matrix

The matrix is an inventory of high-risk operation families. A check means the
named canonical actions exist today; **gap register** means the exact routes
are tracked in `tests/audit_coverage_allowlist.json` and must be migrated in
the referenced follow-up area. That machine-readable register is part of this
matrix and prevents silent additions.

| Area / route family | Mandatory operations | Canonical action(s) | Coverage |
| --- | --- | --- | --- |
| Users (`/api/users`, admin user handlers) | create/update/deactivate/delete | `user.create`, `user.update`, `user.deactivate`, `user.delete` | Covered |
| Authentication (`/auth`, session admin) | passkey/MFA/password lifecycle, login failure, revoke, impersonate | `auth.passkey.registration.succeed`, `auth.passkey.rename`, `auth.passkey.remove`, `auth.session.revoke`, `impersonation.start`, `impersonation.stop` | Passkey/session covered; remaining routes in gap register |
| API keys (`/api/api-keys`) | create/update/rotate/retire/delete | `api_keys.create`, `api_keys.update`, `api_keys.rotate`, `api_keys.retire`, `api_keys.delete` | Covered; legacy helper migration pending |
| Roles and memberships (`/api/roles`, `/api/memberships`) | role/membership CRUD and permission change | `role.create/update/delete`, `membership.create/update/delete`, `user_permissions.update` | Covered; legacy names/helper migration pending |
| Companies (`/api/companies`, `/admin/companies`) | CRUD/archive/restore, assignments, credentials | `company.create/update/delete/archive/restore`, `membership.*` | API core covered; admin/credential routes in gap register |
| Tickets (`/api/tickets`, `/admin/tickets`) | CRUD, reply, assignment/status, watcher, attachment, split/merge/bulk actions | `ticket.create/update/delete/reply/assign/status_change`, `ticket.watcher.add/remove`, `ticket.split`, `ticket.attachment.block` | Core API covered; admin and child-resource routes in gap register |
| Billing/shop (`/api/invoices`, orders, quotes, cart, subscriptions, shop admin) | CRUD, approve, sync, billing, order and subscription change | `invoice.*`, `shop.product.*`, `shop.package.*`, `shop.category.*` | Invoice/shop admin covered; order/quote/cart/subscription routes in gap register |
| Knowledge/configuration | article, template, automation, integration/module and site configuration | `knowledge_base.article.*`, `message_template.*`, `automation.*`, `imap.account.*` | Named APIs covered; module/forms/status/config routes in gap register |
| Staff lifecycle | onboarding/offboarding decisions, workflow override, privileged M365 actions | `staff.onboarding.*`, `staff.offboarding.*`, `staff.workflow.operator.*`, `staff.m365.*` | Decisions covered; staff CRUD/external checkpoint routes in gap register |
| BCP and backups | plan/data CRUD, incident actions, manual exports/reseed, backup/token operations | `bcp.*`, `backup_job.*` | Incident/core items covered; plan subresources/backups in gap register |
| Privileged operations | imports, exports, sync, manual execute/retry, system/demo operations | domain-specific `.import/.export/.sync/.execute/.retry` | Partial; all uncovered route families are in gap register |
| Webhooks | verified material provider state changes | e.g. `invoice.xero_webhook.mark_paid` | Xero covered; SMTP/BCP/integration hooks in gap register |
| Scheduled/background | material state changes, remediation, destructive cleanup | domain-specific action plus `source` and system `actor` | Service call sites reviewed per change; non-route work requires explicit reviewer check |

The gap register includes some POST endpoints which are excluded candidates
(search, preview, health/status). They remain listed until explicitly classified
and documented, rather than silently allow-listed forever.

## Contributor checklist

1. Identify the state transition and capture the persisted entity immediately
   before and after it. Emit only after successful persistence.
2. Select one canonical action from this matrix, or add a new imperative name
   here when the transition is genuinely new.
3. Call `record_create`, `record`, or `record_delete`; supply entity, tenant,
   origin, actor, request, and concise outcome fields as applicable.
4. Exclude message/payload bodies and add domain-sensitive keys to
   `sensitive_extra_keys`. Verify both snapshots and metadata are redacted.
5. Test the representative success, no-op, actor/source attribution, request
   correlation, redaction, and repository-failure path.
6. Run `pytest tests/test_audit_record.py tests/test_audit_diff.py
   tests/test_audit_coverage.py`. Remove a route from the gap register when it
   gains coverage; never add an entry merely to make the test pass without a
   documented excluded-event rationale.

