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

### API Development

- Include CRUD Swagger UI for all API endpoints
- Update API documentation when endpoints are created or modified
- Use RESTful conventions for API design
- Implement rate limiting for external API integrations

## UI and Frontend Guidelines

The canonical layout, table, form, and theming rules live in
[`docs/ui_layout_standards.md`](../docs/ui_layout_standards.md). All new pages
and components must follow that document; the bullets below are a quick
reference and must stay consistent with it.

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
- Fix Possible SQL injection vectors before completing the assigned task
