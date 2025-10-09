# Node.js Feature Parity Gap Analysis

## Overview

The current FastAPI rewrite only delivers a small subset of the legacy Express portal. The Node.js service implemented comprehensive authentication, commerce, forms, licensing, automation, and auditing flows that are absent from the Python codebase, which currently exposes only basic CRUD APIs for users and companies plus a placeholder dashboard view.【F:src/server.ts†L1-L214】【F:app/api/routes/users.py†L1-L58】【F:app/api/routes/companies.py†L1-L48】【F:app/templates/dashboard.html†L1-L21】

## Missing Feature Inventory

| Domain | Legacy Node.js Capability | Python Status | Parity Gap |
| --- | --- | --- | --- |
| Authentication & Sessions | Redis-backed Express sessions, CSRF middleware, login throttling, trusted devices, and TOTP enrolment during login and admin management.【F:src/server.ts†L1060-L1119】【F:src/server.ts†L1502-L1619】 | FastAPI now exposes session-based login/logout, CSRF middleware, rate limiting, password reset tokens, and TOTP enrolment with documented endpoints.【F:app/api/routes/auth.py†L1-L282】【F:app/main.py†L1-L74】【F:README.md†L33-L65】 | Implement device trust cookies, remembered browser flows, and OAuth social logins to match the remaining Node.js capabilities.
| Company & User Operations | Extensive multi-company assignments, role toggles, staff permissions, and audit logging across administrative views.【F:src/server.ts†L44-L214】 | Only direct user/company CRUD helpers exist without assignments, roles, or audit trails.【F:app/repositories/users.py†L1-L64】【F:app/repositories/companies.py†L1-L57】 | Implement company membership models, role management, audit logging, and admin UIs.
| Staff & Syncro Integration | Syncro customer/contact/asset importers and staff verification workflows kept personnel data in sync.【F:src/server.ts†L489-L520】【F:src/views/staff.ejs†L1-L40】 | No staff repositories, Syncro client, or import jobs exist in Python.【F:app/api/routes/users.py†L1-L58】 | Rebuild staff data models, Syncro API client, import schedulers, and UI for managing staff.
| Licensing & Microsoft 365 | License CRUD plus background cron jobs and OAuth flows to sync Microsoft 365 tenant data.【F:src/server.ts†L37-L214】【F:src/server.ts†L1959-L2144】 | No license tables, services, or M365 endpoints are present.【F:app/api/routes/companies.py†L1-L48】【F:app/main.py†L49-L61】 | Restore license schemas, sync services, OAuth callbacks, and related UI tabs.
| Commerce (Shop, Products, Orders) | Customer-facing shop, admin catalogue, Discord stock alerts, SMS webhooks, and comprehensive REST/Swagger APIs for products, categories, orders, and pricing rules.【F:src/views/shop-admin.ejs†L1-L80】【F:src/server.ts†L430-L484】【F:src/server.ts†L2730-L3208】【F:src/server.ts†L4431-L4520】 | FastAPI now serves the product catalogue with VIP pricing, persistent carts, order placement, webhook delivery, and Discord stock notifications alongside the shop admin UI.【F:app/main.py†L1620-L1890】【F:app/templates/shop/index.html†L1-L200】【F:app/templates/shop/cart.html†L1-L120】【F:app/services/shop.py†L1-L76】 | Extend REST APIs for orders, pricing rules, and shipping updates; restore SMS fulfilment alerts and admin order history views.
| Forms & OpnForm Integration | Dedicated forms admin/assignment UI with OpnForm launch links and permission mapping.【F:src/views/forms-admin.ejs†L1-L66】【F:src/server.ts†L112-L121】【F:src/server.ts†L3514-L3561】 | No form entities or integration endpoints exist in Python.【F:app/templates/dashboard.html†L1-L21】【F:app/api/routes/companies.py†L1-L48】 | Recreate form repositories, company/user assignment APIs, and OpnForm embedding.
| Assets, Invoices & Orders | Asset/invoice CRUD, Syncro asset upserts, and order fulfilment views with PO tracking and SMS subscriptions.【F:src/server.ts†L101-L166】【F:src/server.ts†L285-L334】【F:src/server.ts†L4860-L5096】 | No equivalent repositories or endpoints exist.【F:app/repositories/companies.py†L1-L57】【F:app/api/routes/users.py†L1-L58】 | Implement asset & invoice models, order workflows, and notification pipelines.
| Automation & Scheduled Tasks | Cron-backed scheduled task manager with admin UI and dynamic job loader.【F:src/server.ts†L23-L25】【F:src/server.ts†L486-L520】【F:src/server.ts†L912-L933】 | No scheduling utilities or admin pages exist.【F:app/main.py†L19-L61】【F:app/templates/base.html†L1-L33】 | Add scheduler service, task persistence, admin controls, and monitoring UI.
| API Keys & Audit Trails | API key issuance/usage logging and audit log viewer exposed via REST and admin pages.【F:src/server.ts†L92-L104】【F:src/views/admin.ejs†L1-L60】 | FastAPI now exposes hashed API key creation, revocation, usage telemetry, inline rotation workflows, and cross-service correlation dashboards with Swagger-managed endpoints.【F:app/api/routes/api_keys.py†L1-L180】【F:app/repositories/api_keys.py†L1-L205】【F:app/main.py†L405-L692】【F:app/main.py†L1915-L2168】【F:app/templates/admin/api_keys.html†L1-L421】 | Expand anomaly detection with automated alerting and SIEM/webhook export parity.

## Suggested Restoration Tasks

1. **Foundation & Security**
   - Introduce session-backed authentication with secure cookies, CSRF middleware, password resets, and MFA as described above.【F:src/server.ts†L1060-L1619】
   - Recreate rate limiting, trusted device cookies, and login lockout policies for parity with the legacy protections.【F:src/server.ts†L1502-L1569】

2. **Domain Data Models & Repositories**
   - Port database schemas and repository logic for staff, licenses, forms, products, orders, assets, invoices, API keys, and audit logs.【F:src/server.ts†L44-L214】【F:src/views/shop-admin.ejs†L1-L80】
   - Implement corresponding Pydantic schemas and FastAPI routers to serve CRUD plus business workflows for each domain.【F:app/api/routes/users.py†L1-L58】【F:app/api/routes/companies.py†L1-L48】

3. **External Integrations**
   - Restore Syncro and Microsoft Graph clients, along with background jobs and webhook/cron infrastructure for data synchronisation.【F:src/server.ts†L23-L37】【F:src/server.ts†L1959-L2144】
   - Rebuild notification pipelines for SMTP, Discord webhooks, and SMS shipping updates, including retry/monitoring dashboards.【F:src/server.ts†L259-L283】【F:src/server.ts†L285-L334】【F:src/server.ts†L430-L484】

4. **Frontend & Admin Experiences**
   - Recreate the comprehensive EJS views (shop, forms, staff, admin dashboards) using Jinja templates aligned with the new layout system.【F:src/views/forms-admin.ejs†L1-L66】【F:src/views/shop-admin.ejs†L1-L80】【F:app/templates/base.html†L1-L33】
   - Reinstate filtering, sorting, and responsive controls for tables, plus OpnForm embedding and admin change logs.【F:src/views/forms-admin.ejs†L27-L62】【F:src/views/shop-admin.ejs†L54-L80】

5. **Automation & Observability**
   - Build a scheduler service to persist and execute cron definitions, mirroring the legacy ability to dynamically register tasks and track execution.【F:src/server.ts†L486-L520】【F:src/server.ts†L912-L933】
   - Restore audit logging, API key usage tracking, and administrative monitors for webhook retries and scheduled job outcomes.【F:src/server.ts†L92-L104】【F:src/server.ts†L285-L334】

Completing these tasks will close the parity gap and reinstate the enterprise workflows that customers relied on in the Node.js portal.
