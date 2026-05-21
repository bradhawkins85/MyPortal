# Logging & Audit Trail

MyPortal records two parallel streams of activity that together support
operational troubleshooting and compliance review:

| Stream | What it captures | Where it lives |
| --- | --- | --- |
| **Server logs** | Every HTTP request, every emitted log line, every unhandled exception | Console + optional disk file (Loguru) |
| **Audit logs** | Every meaningful change a user makes to data (create / update / delete / replied / assigned …) | `audit_logs` MySQL table + disk audit sink |

The two streams are linked through the **request ID** (`X-Request-ID`)
so you can pivot from a stack trace in the log file to the exact audit row
that caused it.

---

## Server logs

Configured in `app/core/logging.py`. Enrichment, sinks and rotation are all
controlled via environment variables — see `.env.example` for the full
list. The most useful ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FAIL2BAN_LOG_PATH` | _unset_ | Path to the main disk log file. When set, the application writes a structured Loguru sink that Fail2ban / SIEM tools can tail. |
| `LOG_ROTATION` | `50 MB` | Loguru rotation policy. Accepts a size (`50 MB`), an interval (`1 day`), or a clock time (`00:00`). Set empty to disable. |
| `LOG_RETENTION` | `30 days` | How long rotated log files are kept. |
| `LOG_COMPRESSION` | `gz` | Compression format for rotated files. Set empty to keep them uncompressed. |
| `ERROR_LOG_PATH` | _unset_ | Optional second sink that receives **WARNING and above only**. Useful for tailing "just the bad stuff". Inherits the same rotation settings. |

### Context enrichment

Every log line emitted while a request is being handled is automatically
tagged with:

- `request_id` — the value of the `X-Request-ID` header (generated if not
  supplied by the client). Returned to the caller in the response.
- `user_id` — populated by the authentication dependencies
  (`get_current_user` / `get_optional_user`) once the request is identified.
- `route` — the matched route path, e.g. `/api/tickets/{ticket_id}`.
- `client_ip` — the originating IP, honouring `X-Forwarded-For` when present.

These are all bound through `contextvars`, so they survive across
`async`/`await` hops within the request and are cleared automatically when
the response is sent.

### Pivoting from log to audit row

1. Spot the failing request in the log file. Each line includes
   `request_id=<uuid>`.
2. Open `/admin/audit-logs` and paste the request ID into the **Request ID**
   filter.
3. The matching audit row(s) will show the actor, action, and a per-field
   diff of what they were trying to change.

---

## Audit logs

The canonical recording API is **`app.services.audit.record(...)`**. Use it
instead of `log_action` for any new code:

```python
from app.services import audit as audit_service

await audit_service.record(
    action="ticket.update",
    request=request,                # picks up request_id, IP, etc.
    user_id=int(current_user["id"]),
    entity_type="ticket",
    entity_id=ticket_id,
    before=existing_ticket_dict,
    after=updated_ticket_dict,
    metadata={"reason": "Customer requested escalation"},
    sensitive_extra_keys=("body",), # optional; supplements built-in redaction
)
```

What the helper does for you:

- **Field-level diff** — only fields whose value actually changed are
  written to `previous_value` / `new_value`. No-op updates are skipped.
- **Secret redaction** — keys matching well-known sensitive patterns
  (`password`, `password_hash`, `token`, `secret`, `api_key`,
  `client_secret`, `totp_secret`, …) are replaced with `***REDACTED***`.
  Pass extra keys via `sensitive_extra_keys` for domain-specific values
  (e.g. ticket reply `body`).
- **Context propagation** — `request_id`, `user_id`, IP and API key are
  pulled from the request context if not supplied explicitly.
- **Failure isolation** — if the database write fails the user request is
  not affected; the failure is logged to disk for investigation.

### Action taxonomy

Action strings follow `<entity>.<verb>` (lowercase, dot-separated). The
following are in use today; please reuse them when adding new audit calls:

| Domain | Actions |
| --- | --- |
| Users | `user.create`, `user.update`, `user.delete` |
| Companies | `company.create`, `company.update`, `company.delete`, `company.archive`, `company.unarchive` |
| Tickets | `ticket.create`, `ticket.update`, `ticket.status_change`, `ticket.assign`, `ticket.replied`, `ticket.deleted`, `ticket.watcher.add`, `ticket.watcher.remove` |
| Billing | `invoice.create`, `invoice.update`, `invoice.delete` |
| Knowledge base | `knowledge_base.article.create`, `knowledge_base.article.update`, `knowledge_base.article.delete` |
| Automations | `automation.create`, `automation.update`, `automation.enable`, `automation.disable`, `automation.delete` |
| Message templates | `message_template.create`, `message_template.update`, `message_template.delete` |
| Integrations | `imap.account.create`, `imap.account.update`, `imap.account.delete` |
| Shop products | `shop.product.create`, `shop.product.update`, `shop.product.delete`, `shop.product.archive`, `shop.product.unarchive`, `shop.product.visibility_change`, `shop.product.import` |
| Shop categories | `shop.category.create`, `shop.category.update`, `shop.category.delete` |
| Shop subscription categories | `shop.subscription_category.create`, `shop.subscription_category.update`, `shop.subscription_category.delete` |
| Shop packages | `shop.package.create`, `shop.package.update`, `shop.package.archive`, `shop.package.unarchive`, `shop.package.delete`, `shop.package.item.add`, `shop.package.item.update`, `shop.package.item.remove`, `shop.package.item.alternate.add`, `shop.package.item.alternate.remove` |
| Shop optional accessories | `shop.optional_accessory.sync`, `shop.optional_accessory.import`, `shop.optional_accessory.dismiss`, `shop.optional_accessory.bulk_dismiss`, `shop.optional_accessory.restore` |

> Convenience wrappers `audit_service.record_create(...)` and
> `audit_service.record_delete(...)` are available for the common
> "before is None" / "after is None" cases — they make the intent obvious at
> the call site and route through `record(...)` so the same diff and
> redaction logic applies.

### Redaction rules

- Built-in patterns: `password`, `password_hash`, `pwd`, `passcode`,
  `secret`, `client_secret`, `signing_secret`, `webhook_secret`,
  `token`, `access_token`, `refresh_token`, `id_token`, `bearer`,
  `auth`, `authorization`, `api_key`, `apikey`, `api-key`,
  `private_key`, `encryption_key`, `totp_secret`, `mfa_secret`,
  `session_secret`, `cookie_secret`.
- Any string longer than 500 characters is truncated to keep audit rows
  readable.
- `Decimal`, `datetime`, `date`, `UUID` and other non-JSON-native types are
  normalised to JSON-friendly representations before storage.

### Ticket reply body — never stored

`ticket.replied` audit rows record only metadata (`reply_id`, `author_id`,
`channel` (public/internal), `is_billable`, `minutes_spent`, `length`,
`word_count`). The body is **never** persisted to `audit_logs`, even if a
caller accidentally adds it to `metadata`, because we register `"body"` as
a sensitive extra key for that audit call.

This is enforced by a regression test
(`tests/test_audit_record.py::test_record_never_stores_ticket_reply_body`).

---

## Retention

`audit_logs` rows older than `AUDIT_RETENTION_DAYS` (default 365) can be
pruned via `app.repositories.audit_logs.prune_audit_logs(...)`. Disk logs
rotate and expire automatically through the Loguru `LOG_RETENTION` setting.

---

## Admin UI

`/admin/audit-logs` (super_admin only):

- **Filters**: free-text search, action (with autocomplete from recent
  actions), entity type / id, user id, IP address, request id, date range,
  page size.
- **Per-field diff**: each row expands to a `Field / Previous / Current`
  table so admins don't need to read raw JSON. Changed fields are
  highlighted; legacy rows that stored full snapshots still render in the
  same format.
- **Pagination**: server-side paging using `limit` + `offset`.

The same dataset is available programmatically at `GET /api/audit-logs`
with the same filter parameters.
