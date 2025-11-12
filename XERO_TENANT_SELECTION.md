# Xero Tenant Selection Feature

## Overview
This feature allows administrators to change the Xero tenant (organization) through the web UI without requiring environment variable changes or application restarts.

## Implementation Details

### API Endpoint
- **URL**: `GET /api/integration-modules/xero/tenants`
- **Authentication**: Requires super admin access
- **Returns**: List of available Xero organizations with their IDs and names

### UI Changes
The Xero module settings page now includes:
1. **Tenant Selector Dropdown**: Lists all available Xero organizations
2. **Load Tenants Button**: Fetches organizations from Xero API
3. **Manual Entry Toggle**: Allows direct entry of tenant ID for advanced users
4. **Optional Company Name Field**: No longer required for tenant selection

### User Workflow

#### Method 1: Using Dropdown (Recommended)
1. Navigate to **Admin → Modules**
2. Expand the **Xero** module section
3. Click the **"Load Tenants"** button
4. Select desired organization from the dropdown
5. Click **"Save settings"**

#### Method 2: Manual Entry
1. Navigate to **Admin → Modules**
2. Expand the **Xero** module section
3. Click the **"Manual Entry"** button
4. Enter the Xero tenant ID directly
5. Click **"Save settings"**

### Technical Notes

#### Prerequisites
Before loading tenants, ensure:
- Xero module is enabled
- Client ID is configured
- Client Secret is configured
- Refresh Token is configured

#### Tenant ID Discovery
The endpoint uses the Xero Connections API to fetch available organizations:
- Makes authenticated request to `https://api.xero.com/connections`
- Returns all organizations the user has access to
- Each organization includes:
  - `tenant_id`: Unique identifier
  - `tenant_name`: Organization name
  - `tenant_type`: Usually "ORGANISATION"
  - `created_date_utc`: Creation timestamp

#### Form Submission
The UI intelligently manages which field (dropdown or manual input) is submitted based on the current mode, ensuring only one tenant_id value is saved.

### Benefits
- **No Server Restart Required**: Changes take effect immediately
- **No Environment File Edits**: Eliminates need to modify .env files
- **User-Friendly**: Simple dropdown selection instead of technical configuration
- **Multiple Organizations**: Easy switching between different Xero organizations
- **Fallback Option**: Manual entry available if API call fails

### Testing
Comprehensive test coverage includes:
- Module disabled scenarios
- Missing/incomplete credentials handling
- Successful tenant listing
- Error handling for API failures

All tests pass with no security vulnerabilities detected by CodeQL.

### Security Considerations
- Credentials are encrypted before storage
- Access token is automatically refreshed when needed
- API calls use proper authentication headers
- Endpoint requires super admin privileges
- No sensitive data logged

## Migration Notes
Existing installations will continue to work without changes. The company_name field is now optional, so:
- If tenant_id is already set, it will be preserved
- If using XERO_COMPANY_NAME environment variable, it will still be read but is no longer required
- New installations can configure tenant_id directly through the UI

## Troubleshooting

### "No Xero organizations found"
- Verify client credentials are correct
- Ensure refresh token is valid
- Check that the Xero app has access to organizations

### "Xero OAuth credentials incomplete"
- All three fields must be filled: Client ID, Client Secret, Refresh Token
- Verify tokens haven't expired

### Tenant not available in dropdown
- Use "Manual Entry" to enter tenant ID directly
- Verify the organization exists in your Xero account

## Related Files
- `app/api/routes/xero.py`: API endpoint implementation
- `app/templates/admin/modules.html`: UI template
- `app/static/js/admin.js`: JavaScript logic
- `tests/test_xero_api.py`: Test suite
