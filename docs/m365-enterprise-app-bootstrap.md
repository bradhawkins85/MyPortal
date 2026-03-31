# M365 / Entra ID Enterprise App Bootstrap (AADSTS700016)

## Problem

When a Global Admin signs in, Microsoft Entra ID returns:

`AADSTS700016: Application with identifier '<client-id>' was not found in the directory`

This means the tenant cannot find an application object or service principal for the requested `client_id`.

## Why this happens

Common causes:

1. The `client_id` is wrong (typo, stale value, or environment mismatch).
2. The app registration was deleted in its home tenant.
3. The app is single-tenant and cannot be used from other tenants.
4. The login endpoint/authority is wrong for the target cloud or tenant.
5. The enterprise application (service principal) has not been provisioned in the customer tenant yet.

## Can this be created "just by logging in as Global Admin"?

Yes, but only if the underlying app registration exists and is configured as multi-tenant. On first successful admin-consent flow, Entra ID creates the enterprise application in that tenant automatically.

If the app registration does not exist (or the `client_id` is invalid), login alone cannot create it.

## Lowest-friction onboarding options

### Option 1 (recommended): Admin consent URL

Send the customer Global Admin a single URL:

```text
https://login.microsoftonline.com/{tenant-id-or-domain}/v2.0/adminconsent?client_id={client-id}&redirect_uri={url-encoded-redirect-uri}
```

Outcomes:

- Creates enterprise app (service principal) in the customer tenant (if missing).
- Grants requested delegated app permissions.
- Keeps onboarding to a single click for tenant admins.

Security notes:

- Use exact redirect URI registered on the app.
- Keep scopes minimal and review required permissions.
- Prefer verification/publisher domain alignment so consent prompts are trusted.

### Option 2: Pre-provision with Azure CLI (admin-run)

A Global Admin can create the service principal directly:

```bash
az login --tenant <tenant-id>
az ad sp create --id <client-id>
```

Then grant admin consent through your normal sign-in/consent flow (or Graph/portal).

Notes:

- Works only when `<client-id>` refers to a valid app registration.
- If app is single-tenant in another tenant, this fails.

### Option 3: Microsoft Graph (automation)

For automated onboarding scripts, create the service principal via Graph in customer tenant context:

- `POST /servicePrincipals` with `{ "appId": "<client-id>" }`

This is functionally similar to `az ad sp create --id`.

In MyPortal, this option is available from the **Microsoft 365** provisioning flows as **Option 3 (Graph)** for mapped tenants.

## Implementation checklist for this app

1. Validate `AZURE_CLIENT_ID`/tenant settings at startup and fail fast with clear logs.
2. Add an onboarding page/button that generates tenant-specific admin-consent links.
3. Detect `AADSTS700016` and show guided remediation steps.
4. For optional automated onboarding, provide a script path using CLI/Graph with least-privilege docs.
5. Store onboarding audit events for security traceability.

## Quick diagnosis commands

```bash
# Confirm which client ID the app is using
printenv | rg 'AZURE|ENTRA|MICROSOFT'

# In target tenant, check if service principal exists
az login --tenant <tenant-id>
az ad sp show --id <client-id>
```

If `az ad sp show` returns not found, run:

```bash
az ad sp create --id <client-id>
```

and repeat sign-in.
