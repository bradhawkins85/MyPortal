# Xero OAuth2 Integration Guide

MyPortal now supports OAuth2 authentication for the Xero integration, providing a more secure and user-friendly way to connect your Xero account.

## Overview

The Xero integration uses OAuth2 to securely authenticate with Xero's API. Instead of manually entering refresh tokens, administrators can now authorize MyPortal directly through Xero's authorization interface. All tokens are encrypted and stored securely in the database, and access tokens are automatically refreshed when they expire.

## Prerequisites

Before setting up the Xero integration, you need to:

1. **Create a Xero App** in the [Xero Developer Portal](https://developer.xero.com/myapps)
   - Sign in with your Xero credentials
   - Click "New app"
   - Choose "OAuth 2.0" as the authentication type
   - Set the redirect URI to: `https://your-domain.com/xero/callback`
   - Note your **Client ID** and **Client Secret**

2. **Configure Scopes** - Your Xero app needs these scopes:
   - `offline_access` - For refresh token support
   - `accounting.transactions` - For creating invoices
   - `accounting.contacts` - For managing contacts

## Setup Instructions

### 1. Configure Environment Variables

Add your Xero credentials to the `.env` file:

```bash
# Xero OAuth2 integration
XERO_CLIENT_ID=your_xero_client_id
XERO_CLIENT_SECRET=your_xero_client_secret

# Optional configuration
XERO_TENANT_ID=  # Auto-discovered during OAuth flow or when running "Run test"
XERO_COMPANY_NAME=My Company Ltd  # Required for auto-discovery of tenant_id
XERO_DEFAULT_HOURLY_RATE=150.00
XERO_ACCOUNT_CODE=400
XERO_TAX_TYPE=OUTPUT
XERO_LINE_AMOUNT_TYPE=Exclusive
XERO_REFERENCE_PREFIX=Support
XERO_BILLABLE_STATUSES=resolved, closed
XERO_LINE_ITEM_TEMPLATE=Ticket #{ticket_id} - {ticket_subject} - {labour_name}
```

**Note:** The `XERO_REFRESH_TOKEN` environment variable is no longer required and has been removed. Refresh tokens are now obtained automatically through the OAuth flow.

### 2. Enable the Xero Module

1. Log in as a super administrator
2. Navigate to **Admin → Integration Modules**
3. Find the "Xero" module
4. Enable the module
5. Configure the settings (Client ID and Client Secret will be loaded from environment variables)

### 3. Authorize with Xero

1. On the Xero module card, click the **"Connect to Xero"** button or navigate to `/xero/connect`
2. You'll be redirected to Xero's authorization page
3. Sign in to Xero (if not already signed in)
4. Select the organization you want to connect
5. Click **"Authorize"** to grant MyPortal access
6. You'll be redirected back to MyPortal with confirmation

The system will automatically:
- Store the access token and refresh token (encrypted)
- Retrieve your Xero tenant ID
- Update the module configuration

### 4. Verify Connection

After authorization, you can verify the connection by:

1. Going to **Admin → Integration Modules → Xero**
2. Clicking **"Run test"** to validate credentials and auto-discover tenant_id
3. Checking that the module shows "Connected" status

#### Automatic Tenant ID Discovery

When you click "Run test" on the Xero module, MyPortal automatically discovers your Xero tenant ID if:
- You have configured `XERO_COMPANY_NAME` in your `.env` file
- The tenant ID is not already set
- Valid OAuth credentials (client_id, client_secret, refresh_token) are configured

The system will:
1. Call the Xero `/connections` API endpoint
2. Match the tenant by comparing the `tenantName` from Xero with your configured `XERO_COMPANY_NAME` (case-insensitive)
3. Automatically store the discovered `tenant_id` in the module settings

This eliminates the need to manually look up and enter your Xero tenant ID.

## How It Works

### OAuth Flow

```
┌─────────────┐         ┌──────────┐         ┌──────────┐
│   MyPortal  │         │   Xero   │         │ Database │
└─────────────┘         └──────────┘         └──────────┘
      │                      │                     │
      │  1. /xero/connect    │                     │
      ├─────────────────────►│                     │
      │                      │                     │
      │  2. Authorization    │                     │
      │◄─────────────────────┤                     │
      │     Page             │                     │
      │                      │                     │
      │  3. User Approves    │                     │
      ├─────────────────────►│                     │
      │                      │                     │
      │  4. Callback with    │                     │
      │     auth code        │                     │
      │◄─────────────────────┤                     │
      │                      │                     │
      │  5. Exchange code    │                     │
      │     for tokens       │                     │
      ├─────────────────────►│                     │
      │                      │                     │
      │  6. Access token +   │                     │
      │     Refresh token    │                     │
      │◄─────────────────────┤                     │
      │                      │                     │
      │  7. Store encrypted  │                     │
      │     tokens           │                     │
      ├──────────────────────┼────────────────────►│
      │                      │                     │
```

### Token Refresh

Access tokens expire after a certain period (typically 30 minutes). MyPortal automatically handles token refresh:

1. Before making a Xero API call, `acquire_xero_access_token()` is called
2. The function checks if the current access token is expired or about to expire (5-minute buffer)
3. If expired, it uses the refresh token to get a new access token
4. The new tokens are encrypted and stored
5. The fresh access token is returned for the API call

### Security Features

- **Encrypted Storage**: All tokens are encrypted using AES-256-GCM before storage
- **CSRF Protection**: OAuth state tokens prevent cross-site request forgery
- **Automatic Expiry Handling**: Expired tokens are automatically refreshed
- **No Manual Token Entry**: Eliminates risk of tokens being exposed in configuration files
- **Separate OAuth State**: Xero OAuth flow uses a dedicated state serializer to prevent confusion with other OAuth flows

## API Reference

### Endpoints

- **GET `/xero/connect`** - Initiates the OAuth2 authorization flow
  - Requires: Super administrator session
  - Redirects to: Xero authorization page
  
- **GET `/xero/callback`** - Handles OAuth2 callback from Xero
  - Query params: `code`, `state`, `error`
  - Redirects to: Admin modules page with success/error message

### Module Functions

The following functions are available in `app.services.modules`:

#### `get_xero_credentials() -> dict[str, Any] | None`
Returns decrypted Xero credentials including tokens.

```python
credentials = await modules_service.get_xero_credentials()
if credentials:
    client_id = credentials.get("client_id")
    refresh_token = credentials.get("refresh_token")  # Decrypted
    access_token = credentials.get("access_token")    # Decrypted
```

#### `acquire_xero_access_token() -> str`
Gets a valid access token, automatically refreshing if needed.

```python
# Use this when making Xero API calls
access_token = await modules_service.acquire_xero_access_token()

# Make API call
async with httpx.AsyncClient() as client:
    response = await client.get(
        "https://api.xero.com/api.xro/2.0/invoices",
        headers={"Authorization": f"Bearer {access_token}"},
    )
```

#### `update_xero_tokens(refresh_token=None, access_token=None, token_expires_at=None) -> None`
Updates stored tokens (automatically encrypts before storage).

```python
await modules_service.update_xero_tokens(
    access_token="new_access_token",
    refresh_token="new_refresh_token",
    token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
)
```

#### `refresh_xero_access_token() -> str`
Manually refresh the access token using the stored refresh token.

```python
try:
    access_token = await modules_service.refresh_xero_access_token()
except RuntimeError as e:
    logger.error(f"Failed to refresh token: {e}")
```

## Troubleshooting

### "Missing client id" Error

**Problem:** The Xero module doesn't have a client ID configured.

**Solution:** 
1. Add `XERO_CLIENT_ID` to your `.env` file
2. Restart the application
3. The module should automatically pick up the configuration

### "Authorization failed" Error

**Problem:** The OAuth authorization failed.

**Possible causes:**
- Invalid client credentials
- Redirect URI mismatch in Xero app settings
- User denied authorization
- Network issues

**Solution:**
1. Verify your Client ID and Client Secret in the Xero Developer Portal
2. Ensure the redirect URI in Xero matches: `https://your-domain.com/xero/callback`
3. Check application logs for detailed error messages

### "Invalid state" Error

**Problem:** The OAuth state token is invalid.

**Possible causes:**
- CSRF attack attempt
- Session expired during authorization
- Cookie issues

**Solution:**
1. Try the authorization flow again
2. Ensure cookies are enabled
3. Check that your `SESSION_SECRET` is configured properly

### Token Refresh Failures

**Problem:** Automatic token refresh is failing.

**Solution:**
1. Check that the refresh token is still valid in Xero
2. Verify the Xero app still has the required scopes
3. Re-authorize the application if necessary
4. Check application logs for detailed error messages

## Migration from Manual Token Entry

If you previously configured Xero with a manually entered refresh token:

1. **Backup existing configuration** - Note your current tenant_id and settings
2. **Update `.env` file** - Remove `XERO_REFRESH_TOKEN` line
3. **Re-authorize** - Follow the authorization steps above
4. **Verify settings** - Ensure tenant_id and other settings are preserved

The new OAuth flow will replace the manually entered token with a securely stored, encrypted token.

## Best Practices

1. **Secure Your Client Secret** - Never commit your `.env` file to version control
2. **Use HTTPS** - Always run MyPortal over HTTPS in production
3. **Monitor Token Expiry** - Check logs for token refresh operations
4. **Regular Testing** - Periodically test the Xero integration to ensure it's working
5. **Rotate Credentials** - If you suspect your credentials are compromised, regenerate them in the Xero Developer Portal and re-authorize

## Support

For additional help:
- Check the [Xero API Documentation](https://developer.xero.com/documentation/)
- Review MyPortal application logs for detailed error messages
- Contact your system administrator

## API Scopes Reference

The Xero integration requires the following OAuth2 scopes:

| Scope | Purpose |
|-------|---------|
| `offline_access` | Required for refresh token functionality |
| `accounting.transactions` | Create and manage invoices, bills, and other transactions |
| `accounting.contacts` | Access and manage contact information |

Additional scopes can be added in your Xero app configuration if your integration needs expand.
