# Company Context Switching

MyPortal allows administrators to switch between multiple companies without re-authenticating. This feature is particularly useful for managed service providers managing multiple client companies.

## Overview

The company context switcher enables users with memberships in multiple companies to:
- Switch between assigned companies seamlessly
- Maintain session authentication across switches
- Access company-specific data and resources
- View company-specific tickets, orders, and information

## Features

- **Sidebar company switcher** - Quick access to switch companies
- **Session persistence** - No re-authentication required
- **Company-specific views** - All data filtered by selected company
- **Audit trail** - Company switches are logged for security

## API Endpoint

**POST /switch-company**

Updates the active company for the authenticated session.

### Request Format

The endpoint accepts multiple formats for flexibility:

#### JSON Request
```json
{
  "companyId": 123,
  "returnUrl": "/dashboard"
}
```

#### Form-Encoded Request
```
companyId=123&returnUrl=/dashboard
```

#### Query String
```
POST /switch-company?companyId=123&returnUrl=/dashboard
```

### Parameters

- **companyId** (required) - The ID of the company to switch to
- **returnUrl** (optional) - URL to redirect to after switching (default: current page)

### CSRF Protection

A valid CSRF token is required for authenticated browsers:
- Send as `_csrf` form field, or
- Send as `X-CSRF-Token` header

### Response

On success:
- HTTP 200 or 302 redirect
- Session updated with new company context
- User redirected to `returnUrl` if provided

On error:
- HTTP 403 if user doesn't have access to the company
- HTTP 400 if company ID is invalid
- HTTP 401 if not authenticated

## Company Membership Management

Users must be assigned to companies before they can switch to them. Company memberships are managed by super administrators.

See [Admin Company Memberships](Admin-Company-Memberships) for details on managing company access.

## User Interface

### Sidebar Switcher

The sidebar company switcher appears for users with memberships in multiple companies:

1. Located in the left sidebar navigation
2. Shows currently active company
3. Click to open dropdown of available companies
4. Select company to switch immediately

### Company Information Display

When viewing company-specific data:
- Company name shown in header
- Business information tab shows company details
- All tickets, orders, and resources filtered by company

## Use Cases

### Managed Service Provider (MSP)

An MSP administrator managing 50+ clients can:
1. Log in once with their credentials
2. Switch between client companies as needed
3. View and manage each client's tickets and resources
4. Maintain security context for each company

### Multi-Company Organization

A user working for multiple divisions of an organization can:
1. Access all assigned company contexts
2. Switch between divisions without re-login
3. Keep work segregated by company context

### Support Staff

Support staff with access to multiple customers can:
1. Quickly switch to customer context
2. View customer-specific information
3. Create and manage tickets for different customers

## Security Considerations

### Access Control

- Users can only switch to companies they have membership in
- Company memberships are explicitly granted by administrators
- Attempting to access unauthorized companies results in 403 Forbidden

### Audit Logging

- All company switches are logged with timestamp and user
- Audit trail available for security review
- Helps track access patterns and potential security issues

### Session Isolation

- Each company context maintains separate data views
- No data leakage between company contexts
- Session state properly updated on each switch

## Integration with Other Features

### Tickets

Tickets are filtered by active company:
- Users see only tickets for current company
- Creating tickets automatically associates with current company
- Ticket assignments respect company boundaries

### Orders

Orders are company-specific:
- Shop displays products with company-specific pricing
- Orders are created for the active company
- Order history shows only current company orders

### Licenses

License information is per-company:
- License counts and allocations by company
- Expiry dates tracked per company
- Staff assignments respect company context

## Template Variables

Company context is available in template variables for forms and external integrations:

- `{{company.id}}` - Numeric identifier of active company
- `{{company.name}}` - Name of active company
- `{{company.syncroId}}` - Syncro customer ID (if available)

See the README for complete template variable documentation.

## Related Documentation

- [Admin Company Memberships](Admin-Company-Memberships)
- [Companies API](Companies-API)
- [Authentication API](Authentication-API)
- [Impersonation](Impersonation)
