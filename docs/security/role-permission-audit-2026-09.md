# Role and permission coverage audit (September 2026)

## Scope and method

The review covered the authenticated navigation catalogue, server-rendered pages,
feature-pack routes, and FastAPI endpoints registered by `app.main`. Access checks
were compared with the tri-state role catalogue in
`app.security.menu_permissions`. Existing stored role payloads were not migrated
or rewritten: absent catalogue keys normalise to `none`, so every permission added
by this review defaults to **No Access** for every existing role.

All non-boolean permissions expose **No Access**, **Read Only**, and
**Read/Write**. The existing Technician switch remains intentionally boolean
(**No Access** or **Read/Write**) because it grants an identity/scope capability,
not access to readable records.

## Findings and changes

The following authenticated administration surfaces had super-administrator
checks but no corresponding catalogue entry. Catalogue permissions were added
for users, sessions, benchmarking, RAG diagnostics, the cron calendar, forms
administration, AI tag exclusions, and tray administration. These remain
super-administrator-only; the entries make the inventory explicit and do not
expand any existing role.

The Windows Defender page and its API mutations already advertised a tri-state
permission, but the shared context helper only enforced the permission for write
operations. It now requires read access for the page and read/write access for
mutations. Company ownership or an authenticated session alone can no longer
bypass an explicit **No Access** setting.

The RAG relationship metrics endpoint previously had no authentication
dependency. It now uses the same super-administrator requirement as its owning
RAG administration page.

## Exclusions

The following routes are deliberately excluded from role assignment:

* Login, registration, password recovery, TOTP enrolment, static/PWA assets, and
  health probes must remain reachable before an authenticated role exists.
* OAuth callbacks, inbound vendor webhooks, and tray-agent callbacks authenticate
  with signed state, provider secrets, API keys, or device tokens rather than an
  interactive user's company role.
* Super-administrator system operations (application configuration, feature-pack
  lifecycle, global integrations, and infrastructure diagnostics) retain their
  existing super-administrator check. Catalogue entries marked `admin_only`
  inventory their page ownership but do not delegate those operations to company
  roles.
* Business Continuity uses its established plan-level viewer/editor/approver/admin
  RBAC in addition to the `menu.continuity` navigation permission; replacing that
  finer-grained model with the menu permission would reduce access control.
* Per-record access rules (ticket ownership, form assignments, company scope, and
  report grants) remain additive restrictions after the matching menu permission.

## Regression expectations

Permission normalization must continue to produce `none` for an absent new key.
Read access may retrieve the relevant page or records but must not invoke a
mutation. Read/write access may mutate only within the user's active company and
any existing record-level scope. Super administrators retain their existing
unrestricted access.
