# MyPortal

MyPortal is a Python-first customer portal built with FastAPI, async database access, and Jinja templates for a full web UI.

## What the app does

MyPortal combines customer operations, service delivery workflows, and integrations in one platform.

### Core functionality
- Authentication, registration, API key management, and role-aware access
- Company and staff management workflows
- Ticketing with replies, status tracking, and automation support
- Shop, orders, subscriptions, and invoicing pages
- Knowledge base and help content for users
- Reporting and PDF export support
- Business continuity planning (BCP) and compliance tooling

### Integrations and modules
- Xero
- IMAP/SMTP workflows
- Uptime Kuma
- Syncro
- Tactical RMM
- Microsoft 365 module support
- Webhook/event handling and automation modules

## Key UI areas (screenshots)

> Replace the placeholders below with real screenshots from your environment.

### Dashboard
Placeholder: add a dashboard screenshot here.

### Tickets
Placeholder: add a tickets page screenshot here.

### Admin modules
Placeholder: add an admin modules screenshot here.

### Reporting
Placeholder: add a reporting page screenshot here.

### Shop / Orders
Placeholder: add a shop/orders screenshot here.

## Getting started

1. Create and activate a virtual environment
2. Install dependencies:
   ```bash
   pip install -e .
   ```
3. Copy environment configuration:
   ```bash
   cp .env.example .env
   ```
4. Run the app:
   ```bash
   python -m uvicorn app.main:app --reload
   ```

Default local URLs:
- Portal UI: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Development notes

- Run tests from repo root:
  ```bash
  pytest
  ```
- Additional setup and deployment guidance is included with the repository documentation.
