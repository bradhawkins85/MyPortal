# Dashboard

The portal home page (`/`) is a single, opinionated, server-rendered
dashboard. It replaces the previous customisable card grid (which exposed an
`/api/dashboard/...` namespace, a per-user JSON layout stored in
`user_preferences`, and a JavaScript drag-and-drop editor).

There is no "add card" / "edit layout" flow, no JavaScript, and no client-side
state. The page is rebuilt on every request from a single function call.

## Sections

The view template `app/templates/dashboard.html` renders, in order:

1. **Greeting** — name, role, active company, optional "Switch company" link.
2. **Needs your attention** — a single grouped list of things the user can
   act on right now. Empty state shows "all clear".
3. **Quick actions** — small button row, gated by permissions.
4. **Recent activity** — last few notifications for the current user, plus
   (when present) recent change-log entries.
5. **System health** — super admin only: webhook queue depth + failures, with
   links to `/admin/webhooks` and `/service-status`.

Sections that the user is not allowed to see are simply omitted from the
mapping returned by the builder; the template renders only what's present.

## Building the data

`app/services/dashboard.py` exposes a single coroutine:

```python
async def build_dashboard(request: Request, user: Mapping[str, Any]) -> dict[str, Any]:
    ...
```

It resolves the per-request context (active company, membership row,
super-admin flag, available companies) once, then calls each section
builder. Every section builder reuses the existing repositories
(`tickets_repo`, `invoices_repo`, `notifications_repo`,
`webhook_events_repo`, `licenses_repo`, `change_log_repo`) — there is
no new data plumbing.

Each section builder is defensive: any repository exception is logged
via `app.core.logging.log_error` and treated as zero / empty, so a
single failure can never produce a 500 on the home page.

## Permissions

Server-side only. Three signals:

* `ctx.is_super_admin` — from `user["is_super_admin"]`.
* `ctx.has_permission(flag)` — true for super admins, otherwise reads the
  active membership row (`can_manage_invoices`, `can_manage_assets`,
  `can_manage_licenses`, `can_manage_staff`, …).
* `ctx.active_company_id` — required for any section scoped to a single
  company (invoices, licenses).

If a section's data depends on a permission the user lacks, the
section builder should return early *before* hitting any repository.

## Adding a new section

1. Add an `if`-guarded coroutine call in `build_dashboard` that populates
   a key on the returned mapping. Do its permission check first.
2. Render that key in `app/templates/dashboard.html` inside its own
   `<article class="card card--panel">` wrapper.
3. (Optional) add a unit test in `tests/test_dashboard.py` that monkey
   patches the relevant repository.

That's all — no registry, no JavaScript, no API endpoint. If a future
section legitimately needs partial refresh, add a single
`GET /api/dashboard/summary` endpoint at that point; do not pre-build
the API surface.
