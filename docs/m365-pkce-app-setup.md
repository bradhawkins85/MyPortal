# M365 PKCE App Setup

## Overview

MyPortal uses a **multi-tenant PKCE public-client app registration** in your CSP / Lighthouse partner tenant to drive the tenant-discovery and provisioning OAuth sign-in flows.  Because it is a public client (no secret), it uses PKCE (Proof Key for Code Exchange, RFC 7636) to secure the authorization code exchange.

This app is created **automatically** the first time you provision the M365 integration via **Admin → Modules → m365-admin → Re-provision PKCE app**.  The provisioned Application (client) ID is stored in the module settings and used for all subsequent sign-in flows.

---

## When is re-creation needed?

You may need to re-create the PKCE app if:

- The app registration was accidentally deleted from Azure AD.
- You see an **AADSTS700016** error: *"Application with identifier … was not found in the directory"*.
- The module reports *"No PKCE app is configured"* in the settings panel.
- You are setting up a new MyPortal instance and the partner tenant has already been provisioned for another instance.

---

## Option 1 – Automatic re-provisioning (recommended)

1. Sign in to MyPortal as a super-admin.
2. Go to **Admin → Modules**.
3. Click **Settings** next to the **M365 Admin** module.
4. Ensure the **Application (client) ID** and **Client secret** fields contain valid CSP / Lighthouse partner app credentials.
5. Click **Re-provision PKCE app**.
6. You will be redirected to Microsoft's sign-in page.  Sign in with your CSP / Lighthouse partner tenant **Global Administrator** account and grant consent.
7. MyPortal stores the new PKCE Application (client) ID automatically and displays it in the module settings.

> **Note:** The "Re-provision PKCE app" button calls `/admin/csp/provision`, which creates a fresh PKCE public-client registration in your partner tenant and stores its `appId` in the m365-admin module settings.

---

## Option 2 – PowerShell script

A ready-to-use script is provided at `scripts/create-m365-pkce-app.ps1`.

### Prerequisites

- PowerShell 7+ (or Windows PowerShell 5.1)
- Either the **Microsoft.Graph PowerShell SDK** or the **Azure CLI** installed

```powershell
# Install the Microsoft.Graph SDK (once)
Install-Module Microsoft.Graph -Scope CurrentUser
```

### Usage

```powershell
.\scripts\create-m365-pkce-app.ps1 -RedirectUri "https://portal.example.com/m365/callback"
```

Replace `https://portal.example.com/m365/callback` with the actual URL of your MyPortal `/m365/callback` endpoint.

Additional options:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `-RedirectUri` | OAuth redirect URI (required) | — |
| `-DisplayName` | App display name | `MyPortal Bootstrap` |
| `-TenantId` | Partner tenant ID or domain | Current signed-in tenant |

After the script completes, it prints the new **Application (client) ID**.  Copy this value and either:
- Set `M365_PKCE_CLIENT_ID=<id>` in your MyPortal `.env` file, **or**
- Enter it via the module settings form under *Admin → Modules → m365-admin*.

---

## Option 3 – Azure portal (manual)

1. Sign in to the [Azure portal](https://portal.azure.com) with your CSP / Lighthouse partner tenant **Global Administrator** account.
2. Go to **Azure Active Directory → App registrations → New registration**.
3. Fill in the registration form:
   - **Name**: `MyPortal Bootstrap` (or any descriptive name)
   - **Supported account types**: *Accounts in any organizational directory (Any Azure AD directory – Multitenant)*
   - **Redirect URI**: select **Public client/native (mobile & desktop)** and enter your portal's callback URL, e.g. `https://portal.example.com/m365/callback`
4. Click **Register**.
5. On the app's **Authentication** blade:
   - Scroll to **Advanced settings**.
   - Set **Allow public client flows** to **Yes**.
   - Click **Save**.
6. Copy the **Application (client) ID** from the **Overview** blade.
7. Set `M365_PKCE_CLIENT_ID=<id>` in your MyPortal `.env` file.

---

## Option 4 – Azure CLI (one-liner)

```bash
# Sign in to your partner tenant
az login --tenant <partner-tenant-id>

# Create the multi-tenant public-client app registration
az ad app create \
  --display-name "MyPortal Bootstrap" \
  --sign-in-audience AzureADMultipleOrgs \
  --public-client-redirect-uris "https://portal.example.com/m365/callback" \
  --is-fallback-public-client true \
  --query appId -o tsv
```

The command outputs the new Application (client) ID.  Set it as `M365_PKCE_CLIENT_ID` in your `.env` file.

---

## Environment variable reference

| Variable | Description |
|----------|-------------|
| `M365_PKCE_CLIENT_ID` | Manually configured PKCE public-client app ID.  Overrides the auto-provisioned value stored in the `m365-admin` module settings. |

If both the module-stored value and `M365_PKCE_CLIENT_ID` are absent, MyPortal falls back to the well-known **Azure CLI public client** (`04b07795-8542-4ab8-9e00-81f6b0a2c83a`). This fallback is blocked by most conditional-access policies in real CSP / Lighthouse tenants, so configuring a dedicated PKCE app is strongly recommended.

---

## Required app registration settings

| Setting | Value |
|---------|-------|
| Supported account types | Accounts in any organizational directory (Multitenant) |
| Redirect URI type | Public client / native |
| Redirect URI | `https://<your-portal>/m365/callback` |
| Allow public client flows | Yes |
| Client secret | Not required (public client) |
| API permissions | None required on the PKCE app itself – permissions are granted on the provisioned service app |
