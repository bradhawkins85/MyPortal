# Legacy Node.js Retirement Overview

## Current Status

MyPortal is now a Python-first portal that ships exclusively with the FastAPI implementation described in the project README. The legacy Node.js and TypeScript sources have been removed so the repository contains a single application stack and a single dependency workflow to maintain.【F:README.md†L1-L41】【F:changes.md†L1-L40】

## Key Capabilities Preserved in Python

- **Authentication and session security.** The FastAPI service exposes registration, login, logout, password reset, and TOTP flows with secure session cookies, CSRF protection, and rate limiting managed in application middleware.【F:app/api/routes/auth.py†L1-L120】【F:app/main.py†L154-L214】
- **Commerce and shop management.** The Python services deliver the storefront, VIP pricing, shopping cart, and admin tooling through FastAPI views and shop services, maintaining the responsive UI layout required across the portal.【F:app/services/shop.py†L1-L220】【F:app/templates/shop/index.html†L1-L200】
- **Automation and webhook visibility.** Scheduler APIs, webhook retry dashboards, and notification tooling continue to live in the FastAPI stack, ensuring operational monitoring and automated jobs stay accessible via the admin interface.【F:app/api/routes/scheduler.py†L1-L210】【F:app/templates/admin/webhooks.html†L1-L200】

## Operational Notes

- Development now relies on the Python toolchain outlined in the README and the virtual environment bootstrap script; Node tooling such as `npm install` and `tsc` are no longer part of the workflow.【F:README.md†L91-L112】【F:scripts/bootstrap_venv.py†L1-L120】
- Database migrations continue to run automatically at startup, and API documentation remains available through the integrated Swagger UI that documents every CRUD endpoint.【F:README.md†L107-L114】【F:app/main.py†L35-L151】
- Remaining references to the Node.js stack within code comments and configuration docstrings have been retired so the repository messaging reflects the Python-only deployment.【F:app/core/config.py†L12-L19】【F:app/main.py†L175-L182】

The removal of the Node.js codebase eliminates duplication while retaining feature parity inside the Python application, reducing maintenance overhead and simplifying deployment.
