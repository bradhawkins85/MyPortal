# Xero OAuth2 Integration Setup

This guide explains how to configure the Xero integration using OAuth2 authorization code flow.

## Overview

The Xero integration uses OAuth2 authorization code flow for secure authentication. This means:
- No manual token management required
- Tokens are automatically refreshed
- Secure credential storage with encryption
- Compliance with Xero's OAuth2 best practices

## Prerequisites

1. A Xero account with access to create OAuth2 applications
2. Super admin access to your MyPortal instance
3. A publicly accessible redirect URL for OAuth callbacks

## Setup Steps

### 1. Create a Xero OAuth2 Application

1. Go to https://developer.xero.com/
2. Sign in with your Xero account
3. Navigate to "My Apps" and click "Create an app"
4. Fill in the application details:
   - **App name**: MyPortal (or your preferred name)
   - **Integration type**: Web app
   - **Company or application URL**: Your MyPortal base URL
   - **OAuth 2.0 redirect URI**: `https://your-portal-domain.com/xero/callback`
5. Click "Create app"
6. Note down the **Client ID** and **Client Secret**

### 2. Configure MyPortal Environment Variables

Add the following to your `.env` file:

```bash
# Xero OAuth2 credentials
XERO_CLIENT_ID=your_client_id_here
XERO_CLIENT_SECRET=your_client_secret_here

# Optional: Company name for automatic tenant discovery
XERO_COMPANY_NAME=Your Company Name

# Optional: Billing configuration
XERO_DEFAULT_HOURLY_RATE=150.00
XERO_ACCOUNT_CODE=400
XERO_TAX_TYPE=OUTPUT2
XERO_LINE_AMOUNT_TYPE=Exclusive
XERO_REFERENCE_PREFIX=Support
```

**Important**: Do NOT set `XERO_REFRESH_TOKEN` manually. Tokens are obtained automatically through the OAuth flow.

### 3. Authorize the Integration

1. Log in to MyPortal as a super administrator
2. Navigate to **Admin** → **Modules**
3. Enable the **Xero** module
4. Click "Connect to Xero" or navigate to `/xero/connect`
5. You'll be redirected to Xero's authorization page
6. Review the requested permissions and click "Allow access"
7. You'll be redirected back to MyPortal with a success message

### 4. Verify the Connection

After authorization:
- Access token and refresh token are automatically stored (encrypted)
- Tenant ID is automatically discovered and stored
- Token refresh happens automatically when needed

To test the connection:
1. Go to **Admin** → **Modules**
2. Click "Test" on the Xero module
3. Verify the connection status shows as successful

## Token Management

### Automatic Token Refresh

MyPortal automatically refreshes the access token when:
- The token is expired or will expire within 5 minutes
- Any API call is made to Xero

You don't need to manually manage tokens.

### Re-authorization

If you need to re-authorize (e.g., if tokens are revoked):
1. Navigate to `/xero/connect` as super admin
2. Complete the authorization flow again
3. New tokens will replace the old ones

## Scopes

The integration requests the following OAuth2 scopes:
- `offline_access` - For refresh token capability
- `accounting.transactions` - For invoice management
- `accounting.contacts` - For company/contact synchronization

Note: These are the exact scope names used in the code. Xero may show these with different formatting in their UI.

## Security

- All tokens (access and refresh) are encrypted before storage
- Tokens are only accessible to the module service layer
- State parameter prevents CSRF attacks during OAuth flow
- Only super administrators can initiate OAuth authorization

## Troubleshooting

### "Missing client_id" error
- Verify `XERO_CLIENT_ID` is set in your `.env` file
- Restart the application after updating environment variables

### "Authorization failed" error
- Check that the redirect URI in Xero app matches your MyPortal URL exactly
- Ensure your MyPortal instance is accessible at the configured URL
- Verify `XERO_CLIENT_SECRET` is correct

### "Invalid state" error
- This indicates a potential CSRF attack or session issue
- Try clearing your browser cookies and attempting authorization again
- Ensure `SESSION_SECRET` is properly configured in your `.env`

### "No matching tenant found" error
- Set `XERO_COMPANY_NAME` to match your organization name in Xero exactly (case-insensitive)
- Alternatively, find your tenant ID manually:
  1. After completing OAuth authorization, the tenant ID is logged
  2. Or visit the Xero Developer Portal → My Apps → Your App → OAuth 2.0 Credentials
  3. Set `XERO_TENANT_ID` directly in your `.env` file
- You can also check available tenants by viewing the MyPortal logs after authorization

## API Reference

### Endpoints

- `GET /xero/connect` - Initiate OAuth2 authorization flow (requires super admin)
- `GET /xero/callback` - OAuth2 callback handler (called by Xero)
- `POST /api/integration-modules/xero/callback` - Webhook endpoint for Xero events

### Module Functions

- `acquire_xero_access_token()` - Get valid access token (auto-refreshes if needed)
- `refresh_xero_access_token()` - Manually refresh the access token
- `update_xero_tokens()` - Update stored tokens (called by OAuth flow)
- `get_xero_credentials()` - Get decrypted credentials

## Further Reading

- [Xero OAuth2 Documentation](https://developer.xero.com/documentation/guides/oauth2/auth-flow)
- [Xero API Documentation](https://developer.xero.com/documentation/api/api-overview)
- [OAuth2 RFC 6749](https://tools.ietf.org/html/rfc6749)
