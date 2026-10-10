<h1 align="center">MyPortal</h1>

<p align="center">
  <strong>A self-hosted customer portal and service-desk platform for managed service providers.</strong><br />
  Tickets, assets, Microsoft 365, compliance, billing and automation for every customer company, in one place.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: Prosperity 3.0.0" src="https://img.shields.io/badge/license-Prosperity%203.0.0-blue" /></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white" />
  <img alt="MariaDB 10.10+" src="https://img.shields.io/badge/MariaDB-10.10%2B-003545?logo=mariadb&logoColor=white" />
  <img alt="Docker" src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" />
</p>

<p align="center">
  <img src="docs/images/readme/tickets.png" alt="MyPortal ticket workspace" width="900" />
</p>

> [!NOTE]
> MyPortal is largely "vibe coded" and built first for my own internal use. You are welcome to run it and open
> issues, and I'll look at them when I have time. Pull requests are not being accepted at the moment. Many features
> work but have limited testing.

---

## Contents

- [Overview](#overview)
- [Feature tour](#feature-tour)
  - [Service desk](#service-desk)
  - [Sales, shop and billing](#sales-shop-and-billing)
  - [Microsoft 365](#microsoft-365)
  - [Assets, network and staff](#assets-network-and-staff)
  - [Security and compliance](#security-and-compliance)
  - [Automation, AI and administration](#automation-ai-and-administration)
- [Integrations](#integrations)
- [Architecture](#architecture)
- [Installation](#installation)
- [Development](#development)
- [Documentation](#documentation)
- [License](#license)

## Overview

MyPortal gives an MSP and its customers a shared workspace. Technicians and super admins switch between customer
companies from the sidebar. Customer users see only their own company, limited by the roles and permissions you give
them.

- **Multi-company by design.** Company switching, reusable roles, per-company permissions, a "view as role"
  preview, and impersonation for support.
- **Secure by default.** TOTP two-factor enrolment is mandatory for every account. Passkeys (WebAuthn), CSRF
  protection, rate limiting, session management, an encrypted credential vault, and a full audit trail are built in.
- **Modular.** Each area of the app is a hot-reloadable *feature pack*. Integrations are switched on as *modules*,
  and you can add *plugins* without forking.
- **Operable.** Blue/green production installs, one-click upgrades from the portal, a Docker deployment script,
  Redis-backed multi-worker scaling, and a built-in scheduler.

There are no default credentials. On first visit you are sent to registration, and the first account created becomes
the super administrator.

## Feature tour

> Screenshots were taken from a fresh install loaded with MyPortal's built-in demo data.

### Service desk

| | |
|---|---|
| <img src="docs/images/readme/ticket-detail.png" alt="Ticket detail" /> | <img src="docs/images/readme/issue-tracker.png" alt="Issue tracker" /> |
| **Ticket detail:** a rich-text reply editor, internal notes, billable time and labour types, resolution steps, AI summaries, tasks, linked assets, attachments, and expenses. | **Issue tracker:** follow one problem across many companies, with a separate status for each company. |

- **Tickets.** Saved views, grouping, configurable columns, status summary cards, watchers, split and merge,
  scheduled ticket creation, SLAs and business hours, email tracking (opens, deliveries, bounces), and replies by
  email over IMAP.
- **Knowledge base.** Articles with permission scopes, conditional content, review dates, AI tags, and natural
  language search. Search runs across both documentation and the knowledge base.
- **Service status, forms ([OpnForm](https://opnform.com)), help pages,** and customer **chat**, including a
  [Matrix](docs/wiki/integrations/Matrix%20Chat.md) bridge.
- **Calls and voice.** Click-to-call, call recordings with transcription, and booking calls through Cal.com.

<p align="center"><img src="docs/images/readme/knowledge-base.png" alt="Knowledge base" width="900" /></p>

### Sales, shop and billing

| | |
|---|---|
| <img src="docs/images/readme/shop.png" alt="Shop" /> | <img src="docs/images/readme/subscriptions.png" alt="Subscriptions" /> |
| **Shop:** nested categories, packages, featured products, VIP and standard pricing, upsell and cross-sell, stock by region, and vendor feed imports. | **Subscriptions:** renewal forecasting, co-terming, licence increase and decrease requests, and auto-renew controls. |

- Cart, **quotes**, and **orders** with shipping status notifications.
- **Invoices**, with approval workflow and [Xero](docs/wiki/integrations/Xero%20Integration.md) sync of billable
  ticket time, labour rates, and automatic invoice sending.
- **Marketing pages and email campaigns**, with lead capture.

<p align="center"><img src="docs/images/readme/invoices.png" alt="Invoices" width="900" /></p>

### Microsoft 365

| | |
|---|---|
| <img src="docs/images/readme/m365-best-practices.png" alt="Microsoft 365 best practices" /> | <img src="docs/images/readme/licenses.png" alt="Licences" /> |
| **Best practices:** tenant checks scored against CIS-style benchmarks, with business-impact notes and remediation. | **Licences:** per-SKU totals, allocation, expiry, and auto-renew status. |

- Guided tenant connection through an enterprise app (PKCE). User and shared mailbox reporting, email signature
  management (including classic Outlook), out-of-office, spam search and purge, and diagnostics.
- **Staff onboarding and offboarding** with admin approval. This covers account provisioning, mailbox forwarding and
  delegation, and OneDrive export on offboarding.

### Assets, network and staff

| | |
|---|---|
| <img src="docs/images/readme/assets.png" alt="Assets" /> | <img src="docs/images/readme/expirations.png" alt="Expirations" /> |
| **Assets:** synced from RMM tools or added by hand, with custom fields, photos, warranty tracking, and CSV export. | **Expirations:** one view of upcoming warranty and knowledge-base expiry dates, with reminders. |

- **Network:** network device discovery through subnet scanners, IPAM, rack elevations, an interactive
  network map, and website availability and certificate monitoring.
- **Staff workspace:** a directory with Microsoft 365 sign-in data, custom fields, and onboarding workflows.
  Alongside it are **processes** (tracked runbooks) and **shared credentials** with secure one-time sharing.

<p align="center"><img src="docs/images/readme/staff.png" alt="Staff workspace" width="900" /></p>

### Security and compliance

| | |
|---|---|
| <img src="docs/images/readme/smb1001-compliance.png" alt="SMB1001 compliance" /> | <img src="docs/images/readme/compliance-checks.png" alt="Compliance checks" /> |
| **SMB1001:** tiered certification tracking from Bronze to Diamond, with attestation PDF export and engagement letters. | **Compliance checks:** an assignable library of recurring checks, with status, evidence, and review dates. |

- **Essential Eight** maturity records.
- **Business continuity planning (BCP):** plans, a readiness dashboard, business impact analysis, and an
  assessment library.
- **Windows Defender** health and detections through the tray agent, **DMARC** aggregate reporting, and backup job
  history and daily summaries.
- **Reports:** a company overview PDF, saved reports, and reporting dashboards.

<p align="center"><img src="docs/images/readme/bcp.png" alt="Business continuity overview" width="900" /></p>

<p align="center"><img src="docs/images/readme/company-overview-report.png" alt="Company overview report" width="900" /></p>
<p align="center"><em>Company overview report: a customer-ready summary of staff, orders, Microsoft 365 best practices,
licences, subscriptions and compliance, with PDF download and a report designer.</em></p>

### Automation, AI and administration

| | |
|---|---|
| <img src="docs/images/readme/scheduled-tasks.png" alt="Scheduled tasks" /> | <img src="docs/images/readme/message-templates.png" alt="Message templates" /> |
| **Scheduled tasks:** background jobs with cron schedules, run history, and a calendar view to spot gaps. | **Message templates:** reusable HTML and plain-text templates with variables, used by notifications and automations. |

- **Automations:** scheduled and event-driven workflows, webhook monitoring with retries, and a variable
  system covering tickets, companies, and staff.
- **AI:** [Ollama](https://ollama.com)-powered ticket summaries, tags, and resolution steps. Also a RAG index over
  tickets and the knowledge base, AI quality evaluation, and an
  [MCP server](docs/wiki/integrations/MCP%20Integration.md) for ChatGPT and other MCP clients. AI Ticket Troubleshooter collects log files from devices, analyses, then reports possible solutions.
- **Administration:** companies, users, roles, sessions, impersonation, approvals, API keys, modules, feature packs,
  tray app configuration, system updates, a change log, and an audit trail.

<p align="center"><img src="docs/images/readme/audit-trail.png" alt="Audit trail" width="900" /></p>

## Integrations

| Area | Integrations |
|---|---|
| RMM and PSA | Tactical RMM, Syncro (ticket and company import), Hudu, Huntress |
| Accounting and time | Xero (OAuth2), Solidtime |
| Email | IMAP mailboxes and filters, SMTP and SMTP relay, SMTP2Go (delivery tracking), Microsoft 365 mail |
| Monitoring | Uptime Kuma, Windows Defender (via the tray agent), DMARC reports |
| Messaging and notifications | ntfy, Matrix chat, SMS gateway and inbound SMS, Trello |
| AI | Ollama, ChatGPT and MCP clients |
| Other | OpnForm, Cal.com, Password Pusher, generic webhooks, and an HTTP integration for external tools |

Modules are enabled and configured under **Administration → Modules**.

## Architecture

| Layer | Technology |
|---|---|
| Web and API | Python 3.10+, FastAPI, Uvicorn, Jinja2 templates; OpenAPI docs at `/docs` |
| Data | MariaDB 10.10+ through async drivers, with file-driven SQL migrations in [`migrations/`](migrations) |
| Background work | APScheduler with cron expressions; optional Redis for multi-worker coordination |
| Documents | WeasyPrint (PDF), python-docx |
| Tray agent | Go service and UI for Windows and macOS, plus an Electron chat shell ([`tray/`](tray)) |

```
app/
├── api/routes/     REST API endpoints
├── features/       Hot-reloadable feature packs (tickets, shop, m365_admin, compliance, …)
├── services/       Business logic and integration clients
├── repositories/   Database access
├── templates/      Jinja2 views
└── static/         CSS, JavaScript and images
plugins/            Optional plugins (examples: hello_world, custom_dashboard_widget)
tray/               Cross-platform tray application
deploy/, docker/    nginx, systemd, fail2ban and container deployment assets
```

## Installation

### Production (Ubuntu 24.04 / Debian 12)

The installer sets up nginx on port 80, MariaDB, local Redis, and immutable blue/green releases. It works on bare metal, a VM,
or an LXC container.

```bash
sudo git clone https://github.com/bradhawkins85/MyPortal /opt/myportal/control
sudo /opt/myportal/control/scripts/install_production.sh

# Later updates: use Administration → System Updates in the portal, or
sudo myportal-upgrade
```

See [Setup and Installation](docs/wiki/getting-started/Setup%20and%20Installation.md) and
[Zero Downtime Upgrades](docs/wiki/getting-started/Zero%20Downtime%20Upgrades.md).

### Docker

One script installs Docker if it's missing, then deploys MyPortal from GitHub releases (and later upgrades it). You
don't need to clone the repository.

```bash
curl -fsSLO https://github.com/bradhawkins85/MyPortal/releases/latest/download/myportal-docker.sh
sudo bash myportal-docker.sh install
```

See [Running MyPortal with Docker](docs/wiki/getting-started/Docker.md).

### Development server

```bash
sudo scripts/install_development.sh   # editable install, isolated database, optional service on port 8000
```

## Development

Manual setup needs Python 3.10+ and MariaDB 10.10 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set DB_*, SESSION_SECRET, TOTP_ENCRYPTION_KEY, …
python manage.py migrate --target-release development
python -m uvicorn app.main:app --reload
```

- Portal UI: <http://localhost:8000>. The first visit opens registration for the super admin.
- API docs: <http://localhost:8000/docs>
- Tests: `pytest`
- Redis is optional locally and recommended for production. See [Redis](docs/wiki/getting-started/Redis.md).

A **Demo Company** with sample staff, assets, licences, subscriptions, compliance checks, and issues is seeded
automatically. It's useful for exploring the UI, and it's what the screenshots above use.

## Documentation

| Topic | Where to start |
|---|---|
| Getting started | [Overview](docs/wiki/getting-started/Overview.md) · [Configuration](docs/wiki/getting-started/Configuration.md) · [Tray App](docs/wiki/getting-started/Tray%20App.md) · [Tray Build Server](docs/wiki/getting-started/Tray%20Build%20Server.md) · [Onboarding Wizard](docs/wiki/getting-started/Onboarding%20Wizard.md) |
| Administration | [Message Templates](docs/wiki/administration/Message%20Templates.md) · [Network Map](docs/wiki/administration/Network%20Map.md) · [Company Memberships](docs/wiki/administration/Company%20Memberships.md) · [API Keys](docs/wiki/administration/API%20Keys.md) |
| Tickets | [IMAP Setup](docs/wiki/tickets/IMAP%20Setup.md) · [Email Reply Handling](docs/wiki/tickets/Email%20Reply%20Handling.md) · [Split and Merge](docs/wiki/tickets/Ticket%20Split%20and%20Merge.md) · [Tickets API](docs/wiki/tickets/Tickets%20API.md) |
| Compliance | [SMB1001](docs/wiki/compliance/SMB1001.md) · [Essential 8](docs/wiki/compliance/Essential%208%20Requirements.md) · [Security Overview](docs/wiki/compliance/Security%20Overview.md) |
| Integrations | [Xero](docs/wiki/integrations/Xero%20Integration.md) · [Uptime Kuma](docs/wiki/integrations/Uptime%20Kuma.md) · [MCP](docs/wiki/integrations/MCP%20Integration.md) · [Microsoft 365 app setup](docs/wiki/m365/PKCE%20App%20Setup.md) |
| Developers | [Feature Packs](docs/wiki/developer/Feature%20Packs.md) · [Plugins](docs/wiki/developer/Plugins.md) · [UI Layout Standards](docs/wiki/developer/UI%20Layout%20Standards.md) · [Design Guidelines](docs/wiki/developer/Design%20Guidelines.md) |
| Security | [Passkeys](docs/passkeys.md) · [Fail2ban](docs/wiki/getting-started/Fail2ban%20Setup.md) |

## License

MyPortal is licensed under the [Prosperity Public License 3.0.0](LICENSE), with an additional permission
from the contributor:

- **Free for personal and commercial use.** You can run MyPortal for free, with no time limit, including to
  operate your business and serve your own customers.
- **No resale.** You may not sell, rent, sublicense, or resell MyPortal (or a modified version), offer it as a
  paid hosted product, or bundle it into a product you sell, without a separate written license.

See [LICENSE](LICENSE) for the full terms.
