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

### Dashboard
<img width="1658" height="1235" alt="image" src="https://github.com/user-attachments/assets/266d8362-9183-4b4a-b5af-0e6e895fcd8e" />

### Tickets
<img width="1389" height="1238" alt="image" src="https://github.com/user-attachments/assets/3b0f0a2e-6a97-466e-9b08-65bf74943897" />
<img width="1398" height="1242" alt="image" src="https://github.com/user-attachments/assets/d59d9a00-7f1a-4674-8d23-286abc313f6a" />

### Admin modules
<img width="1393" height="1178" alt="image" src="https://github.com/user-attachments/assets/cd217885-ef19-4ca2-9f14-9feca43122ad" />

### User Account Management and Automated Onboarding/Offboarding - With Admin Approval
<img width="1398" height="562" alt="image" src="https://github.com/user-attachments/assets/f45be91c-a5a0-4ebd-b353-d51136baeb29" />
<img width="1382" height="1235" alt="image" src="https://github.com/user-attachments/assets/ddea9082-a7d8-472f-9d68-98eba1767f0c" />
<img width="1358" height="338" alt="image" src="https://github.com/user-attachments/assets/bfda0738-15f5-49cd-8bca-3af91c04a57e" />

### Reporting
<img width="1392" height="959" alt="image" src="https://github.com/user-attachments/assets/09e75db4-dea7-4eb8-976e-27d96ef54a6f" />
<img width="1393" height="845" alt="image" src="https://github.com/user-attachments/assets/af6af527-abf7-4370-8897-934ff068052c" />
<img width="1396" height="829" alt="image" src="https://github.com/user-attachments/assets/d9a2aab6-1fb7-4b1a-ba7e-b6e56fd05a1b" />
<img width="1383" height="1237" alt="image" src="https://github.com/user-attachments/assets/579c135a-d90f-4fa4-93be-0e972ac40469" />
<img width="1384" height="961" alt="image" src="https://github.com/user-attachments/assets/7c37d701-9d8a-43c3-8a69-563f1641f075" />

### Shop with VIP/Standard Pricing, Upsell/Cross Sell, Vendor Feed Import.
<img width="1393" height="632" alt="image" src="https://github.com/user-attachments/assets/d014686a-d6a5-4250-b002-df1ba4023c84" />
<img width="1382" height="773" alt="image" src="https://github.com/user-attachments/assets/7b29fe28-e343-4f25-88d4-4fcd3f30367d" />

### BCP
<img width="1393" height="1238" alt="image" src="https://github.com/user-attachments/assets/5a768d47-82c9-413b-85b5-427b64ff398d" />

### Essential 8 Compliance
<img width="1385" height="1053" alt="image" src="https://github.com/user-attachments/assets/3074d95d-e695-4ca8-a096-a9eb152b6bb9" />

### Backup Reporting
<img width="1394" height="1240" alt="image" src="https://github.com/user-attachments/assets/356e1632-efa5-409d-92e3-606ecc00d9dd" />

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
