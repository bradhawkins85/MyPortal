# MyPortal Copilot Instructions

## Project Overview

MyPortal is a Python-first customer portal built with FastAPI, async MySQL, and Jinja-powered views. The application provides a modern, extensible architecture for customer management, ticketing, automation, and integrations.

### Technology Stack

- **Backend**: Python 3.10+, FastAPI, SQLAlchemy 2.0+
- **Database**: MySQL (primary), SQLite (fallback)
- **Frontend**: Jinja2 templates, responsive layouts
- **Testing**: pytest, pytest-asyncio
- **Key Dependencies**: uvicorn, aiomysql, aiosqlite, pydantic, pydantic-settings, passlib[bcrypt], pyotp, itsdangerous, redis, httpx, apscheduler, loguru, bleach, weasyprint, cryptography, apprise

The authoritative dependency list lives in `pyproject.toml`; update it there and keep this list in sync.

## Development Workflow

### Running the Application

- Use `python -m uvicorn app.main:app --reload` for development
- Production deployments use systemd services (see `scripts/install_production.sh`)
- Development environment runs alongside production without sharing databases

### Testing

- Run tests with `pytest` from the repository root
- Tests are located in the `tests/` directory
- Always test code changes to prevent Internal Server Error occurrences
- Use existing test patterns (pytest-asyncio, fixtures in conftest.py)
- SQL Migrations should be idempotent
- Prefer the smallest relevant test target for the changed behavior; avoid broad suite runs when a focused check is enough to validate the fix
- Add or update regression tests for bug fixes and new features

### Scope Control and Change Hygiene

- Start with one targeted search and read only the exact files required to confirm the root cause
- Keep changes surgical: do not refactor unrelated code, broaden API contracts, or clean up adjacent modules without a direct need for the task
- If a task spans multiple concerns, split it into smaller, independently verifiable steps instead of bundling unrelated edits
- Do not make speculative fixes based on guesswork; confirm the root cause before coding
- Avoid editing generated, vendored, or minified files unless the task explicitly requires it

### Building and Deployment

- Installation scripts: `scripts/install_production.sh`, `scripts/install_development.sh`
- Update script: `scripts/upgrade.sh`
- Install scripts should:
  - Check and install Python requirements (python3, venv)
  - Create systemd services for running as a service
  - Pull code from GitHub private repos using credentials from .env
  - Set up development installer that doesn't interact with production database

## Code Guidelines

### Language and Framework

- All code should be based on Python
- Use async/await patterns consistently with FastAPI
- Follow existing code structure in `app/` directory
- If the requirement is ambiguous, missing, or conflicts with existing repository conventions, ask for clarification before implementing a broad or speculative fix
- Prefer small, readable, composable functions over clever abstractions that hide state or side effects

### Database and Migrations

- SQL migrations should be applied automatically during application startup
- Use SQLite as fallback when MySQL is not configured
- Store dates and times in UTC format in the database
- Display dates and times in local timezone to users
- Use file-driven SQL migration runner where possible

### Security Best Practices

- Always consider security when implementing changes
- Follow security best practices for authentication and authorization
- The first user created is the super admin
- Login page redirects to registration when no users exist
- Use CSRF protection on authenticated state-changing requests
- Validate and sanitize all user inputs
- Do not commit secrets or credentials to the repository

#### SQL Injection Prevention

- **Never** interpolate user-supplied data—or any variable—directly into a SQL
  string using f-strings, `str.format()`, or `%` formatting.
- **Always** use parameterised queries: pass values as bound parameters to the
  database driver (e.g. `db.execute("… WHERE id = ?", (value,))` for SQLite or
  `db.execute("… WHERE id = %s", (value,))` for MySQL).
- When building dynamic queries (dynamic WHERE clauses, IN-lists, SET clauses),
  only interpolate **static string literals** such as column names you control,
  SQL keywords, and the placeholder character (`?` / `%s`). Never interpolate
  runtime data.
- Avoid f-strings for SQL construction entirely; use plain string concatenation
  (`"WHERE id = " + p`) or `str.join()` so that static analysis tools (Bandit
  B608) can confirm no data reaches the query template.
- Before completing any task, audit every SQL statement in files you have
  touched and fix any potential injection vector.

### API Development

- Include CRUD Swagger UI for all API endpoints
- Update API documentation when endpoints are created or modified
- Use RESTful conventions for API design
- Implement rate limiting for external API integrations

### Feature Packs

Feature packs live at `app/features/<slug>/__init__.py` and are
hot-reloadable via `app.core.features.FeatureRegistry` (see
`docs/wiki/developer/Feature Packs.md`). The `system_update` system
automation hot-reloads packs in-process (no restart, no downtime)
whenever an incoming update's diff is fully scoped to one or more
currently-loaded packs — see
`app/services/scheduler.py::_try_feature_pack_hot_reload`.

Hot-reload triggers on the **file diff**, not on the `PACK.version`
literal. Editing files under `app/features/<slug>/` is enough; the
scheduler will hot-reload those packs and leave everything else
running. Bumping `PACK.version` is **encouraged but no longer
required** — the version is still surfaced in logs and
`/api/features` for traceability, so update it on meaningful changes,
but a forgotten bump will no longer downgrade a pack-only update to a
full restart.

Rules:

- Keep `version` as a **string literal** inside the
  `PACK = FeaturePack(...)` declaration (e.g. `version="1.2.3"`) so
  the scheduler and admin UI can read it. Do not compute it
  dynamically.
- When you do bump `version`, use semantic versioning
  (`MAJOR.MINOR.PATCH`): patch for bug fixes and small tweaks, minor
  for new functionality, major for breaking changes to the pack's
  contract.
- If a change touches both feature-pack code and non-pack code, a
  full restart will be required anyway; bumping the affected pack
  versions in that case keeps the per-pack history accurate.

## UI and Frontend Guidelines

The canonical layout, table, form, and theming rules live in
[`docs/ui_layout_standards.md`](../docs/ui_layout_standards.md). All new pages
and components must follow that document; the bullets below are a quick
reference and must stay consistent with it.

### Gold-Standard Reference Pages

Two pages set the design bar for all new UI work and for any redesign of an
existing page. Study them before building a page, and match their principles
rather than older inline-edit tables or raw form dumps elsewhere in the app:

- **Rack management** — `app/templates/infrastructure/racks.html`,
  `app/templates/infrastructure/_rack_macros.html`,
  `app/static/css/infrastructure.css`, `app/static/js/racks.js`
- **Company staff custom fields** (Edit company → *Staff custom fields*) —
  the list and `#staff-custom-field-modal` editor in
  `app/templates/admin/company_edit.html`, the `.scf-*` rules in
  `app/static/css/app.css`, and `app/static/js/staff_custom_fields_admin.js`

When a request says "make it look like the racks page" or "like the custom
fields editor", these are the files meant. Follow these principles:

#### Show the thing, not the storage format

- Present data as the user thinks about it: the rack is drawn as an elevation
  with front and rear faces; a custom field is shown as a card with its label,
  key, help text and summary chips. Avoid wide tables of editable inputs
  (compare the older *Staff intake fields* table with the *Staff custom
  fields* list).
- Summarise each record with short chips/pills (type, option count,
  "Shown when …", "Restricted visibility", "M365 synced") so its state is
  readable without opening it.
- Use at-a-glance summaries where they help: stat lists (`<dl>` of counts),
  capacity donuts/meters, legends that explain every colour used.

#### List first, edit in a focused modal

- The page shows a scannable, grouped, searchable list; creating or editing
  happens in a modal (`<dialog>` with `showModal()`, or the `.modal` pattern)
  opened from a primary "+ Add …" button or the item itself.
- Group related items under headings (custom fields by group, racks under
  *Cabinets*) and give lists a client-side search with a "No results"
  message.
- Offer *Duplicate* alongside *Edit* when records are commonly similar.

#### Modal/editor anatomy

- **Header**: optional small eyebrow line, the title, and a one-line
  subtitle or context (e.g. the selected rack position). Close button
  top-right.
- **Body**: split into a main form column and an aside with a **live
  preview** (the rendered field, the placement on a mini rack) plus a
  plain-English summary of the rules ("Shown when Department is Sales").
  The preview updates as the user types.
- **Tabs** (`role="tablist"`/`tab`/`tabpanel`, arrow-key friendly) when an
  editor has several concerns, with plain-language names ("Show when",
  "Who sees it") and a dot or count indicating tabs that hold settings.
- **Footer**: destructive action (*Delete*/*Remove*) on the left, a spacer,
  then *Cancel* and the primary *Save* on the right.
- Guard against losing unsaved changes, validate inline next to the
  relevant section (`[data-…-error]` messages), and keep the modal usable on
  small screens.

#### Friendly, structured inputs — never raw strings or JSON

- Use visual choice cards (radio inputs styled as cards with icon, label and
  one-line help) for types and kinds; segmented controls for small
  enumerations (Front/Rear, Half/Full, ⅓/⅔/Full).
- Build lists row by row (option builder with reorder, Enter-to-add) and add
  a "Paste a list" bulk import instead of `value:Label, …` strings.
- Use tag inputs for multi-value text (job titles, emails, mailboxes).
- Express conditions as a sentence builder ("Show when [field] [operator]
  [value]") with operators filtered by the chosen field's type.
- Mark fields as `optional` in a small muted label rather than asterisks
  everywhere; give each input short help text explaining its effect;
  generate derived values (keys from labels) automatically.
- Serialise complex editor state into hidden inputs (e.g. `options_json`) so
  the server gets structured data; keep accepting any legacy format.

#### Helpful empty states and copy

- Empty states say what the feature is for and offer the primary action
  ("No custom fields yet … + Add your first field").
- Write instructions in plain English addressed to the user; explain what
  will happen, not how it is stored.

#### Accessibility and progressive enhancement

- Use semantic landmarks and labelling: `aria-labelledby` on sections,
  `<caption>` on tables, `aria-current="page"` on active nav, `aria-pressed`
  on toggles, `aria-haspopup="dialog"`/`aria-controls` on dialog triggers,
  `role="status" aria-live="polite"` for changing status text, and
  `sr-only`/`visually-hidden` labels for icon-only controls.
- Pages must work before JavaScript runs (`<noscript>` fallbacks, real
  `<form method="post">` actions with CSRF) and JS enhances them.
- Pass server data to scripts via `<script type="application/json">` blocks
  and `data-*` hooks, not inline JS or globals; keep page JS in its own file
  under `app/static/js/`.
- Remember per-viewer UI preferences (collapsed panels, toggles) in
  `localStorage` wrapped in `try/catch`, and render correctly without it.
- Respect `prefers-reduced-motion`, and add responsive breakpoints so
  multi-column workspaces collapse cleanly on tablets and phones.

#### Styling

- Namespace page-specific CSS with a short BEM-style prefix (`.rack-…`,
  `.scf-…`) and build on shared tokens (`var(--space-…)`, `var(--radius-…)`,
  `var(--font-size-…)`, `var(--color-…)`). If a workspace needs its own
  palette, define it once as scoped custom properties on the root element
  (as `.rack-workspace` does with `--rack-*`) — never scatter literal colours.
- Keep reusable drawing pieces (icons, donuts, faces) in a macros file next
  to the template, as `_rack_macros.html` does.

### Layout and Design

- Use the standard 3-part layout (unless otherwise specified):
  - Left **sidebar** with navigation buttons and relevant icons
  - Top **page header** with the page title, optional meta line, and a single
    right-aligned actions area (primary button + overflow "Actions ▾" menu)
  - Right **body** with content cards (`.card.card--panel`) holding the actual
    data, forms, or tables
- Page titles come **only** from the `header_title` block of `base.html`; the
  first card on the page must not repeat the title.
- Page-level actions live in the top-right header area and are rendered with
  the `page_header_actions` macro from `templates/macros/header.html` — never
  as buttons inside a card body.
- Apps should be themeable with custom favicons and logos via `site_settings`.
- Use a responsive layout. Pages must not exceed the viewport width or height
  unless explicitly specified.
- Keep row divider heights consistent across table width.
- Prefer form-based user input over JSON code blocks. For example, use an
  input box for "Ticket Subject" instead of a JSON block such as
  `{"ticket.subject": ""}`, and prefer and/or grouping of supported fields
  (that admins can add/remove) over raw match payloads like
  `{"match": {"ticket.subject": "New Voicemail from 61%"}}`.
- All new components must use the existing CSS custom properties
  (`var(--color-…)`, `var(--space-…)`); no hard-coded colors.

### Tables and Data Display

- Build tables with the macros in `templates/macros/tables.html`
  (`data_table`, `table_toolbar`, `table_column_picker`, `empty_state`).
- Every data table follows the shape
  `[ search ] [ filter ] [ filter ] … [ Columns ▾ ] [ Bulk actions ▾ ]`
  and must support sorting, filtering, and pagination for large datasets.
- Column visibility is persisted automatically via
  `app/static/js/table_columns.js`; new tables should use this generic
  helper rather than per-table column scripts.
- Status pills use `<span class="status status--<variant>">…</span>`
  (variants: `success`, `warning`, `danger`, `info`, `neutral`).
- Render timestamps with `<span data-utc="…">…</span>` so the existing JS
  in `main.js` localises them. Never use `strftime` in templates for
  user-facing dates.

## Change Log Management

### Creating Change Log Entries

- Maintain a change log for each new feature or fix
- Store change logs in the `changes/` folder
- Each change should generate a new file with a GUID as the filename
- Import change log files to the `change_log` database table
- Migrate historical entries from `changes.md` to the database

### Change Log Format

Change log files must use this JSON format:

```json
{
  "guid": "",
  "occurred_at": "",
  "change_type": "",
  "summary": "",
  "content_hash": ""
}
```

## Integration Guidelines

### External APIs

When working with external APIs, use the documentation for these systems:

- **SyncroRMM**: https://api-docs.syncromsp.com/
- **TacticalRMM**: https://api.hawkinsitsolutions.com.au/api/schema/swagger-ui/
- **Xero**: https://developer.xero.com/documentation/api/
- **UptimeKuma**: https://github.com/louislam/uptime-kuma/wiki/API-Documentation
- **Ntfy**: https://docs.ntfy.sh/subscribe/api/

### Webhook Management

- Monitor external webhook calls
- Implement retry logic for failed webhooks
- Make monitoring accessible via admin page
- Track webhook events in the database

## Environment Configuration

### .env File Management

- Create and maintain `.env.example` file with all available environment variables
- Update `.env.example` automatically as new variables are added
- Never commit actual `.env` files with real credentials
- Document each environment variable's purpose

## Code Quality Standards

### File Management

- Do not generate binary files
- Python bytecode cache files should not be committed to the repository
- Use `.gitignore` to exclude build artifacts and dependencies

### Code Style

- When moving items between locations, match the style of the destination rather than the source
- Follow existing code patterns and conventions in the repository
- Write clear, self-documenting code with minimal comments
- Use type hints for function parameters and return values

### Testing Requirements

- Write tests for new features and bug fixes
- Use regression tests where possible
- Ensure tests cover edge cases and error conditions
- Run full test suite before submitting changes
- Fix possible SQL injection vectors before completing the assigned task (see **SQL Injection Prevention** in Security Best Practices above)
