# Authentication API

MyPortal provides comprehensive authentication features including session-based authentication, password management, and TOTP multi-factor authentication.

## Overview

- Session-based authentication with secure cookies and sliding expiration
- Built-in rate limiting and CSRF protection
- Password reset flows
- Optional TOTP multi-factor authentication with QR-code provisioning
- First-time registration automatically creates super administrator

## Authentication Flow

### First-Time Setup

When no users exist in the system:
1. Visit the application at http://localhost:8000
2. You'll be automatically redirected to the registration page
3. The first registered user becomes the super administrator
4. Session is automatically established after registration

### Standard Login

For subsequent users:
1. Navigate to the login page
2. Enter email and password
3. If TOTP is enabled, provide the 6-digit code
4. Session cookie is set upon successful authentication

## API Endpoints

All authentication routes are documented in the interactive Swagger UI at `/docs`.

### Registration

**POST /auth/register**

Creates the first super administrator when no users exist and issues a session cookie.

### Login

**POST /auth/login**

Authenticates credentials (and optional TOTP code) to establish a session and CSRF token.

Request body:
```json
{
  "email": "user@example.com",
  "password": "securepassword",
  "totp_code": "123456"
}
```

### Logout

**POST /auth/logout**

Revokes the active session and clears authentication cookies.

### Session Information

**GET /auth/session**

Returns the current session metadata and user profile.

Response includes:
- User ID and email
- First and last name
- Super admin status
- Active company information
- Session expiry

### Password Management

#### Forgot Password

**POST /auth/password/forgot**

Generates a time-bound password reset token and triggers the outbound notification pipeline.

Request body:
```json
{
  "email": "user@example.com"
}
```

#### Reset Password

**POST /auth/password/reset**

Validates the token and updates the user password with bcrypt hashing.

Request body:
```json
{
  "token": "reset-token-from-email",
  "new_password": "newsecurepassword"
}
```

#### Change Password

**POST /auth/password/change**

Allows an authenticated user to rotate their password after validating the current credential.

Request body:
```json
{
  "current_password": "currentpassword",
  "new_password": "newsecurepassword"
}
```

## TOTP Multi-Factor Authentication

MyPortal supports TOTP-based two-factor authentication compatible with Google Authenticator, Authy, and other TOTP apps.

### List Authenticators

**GET /auth/totp**

Lists active TOTP authenticators for the current user.

### Setup TOTP

**POST /auth/totp/setup**

Generates a pending TOTP secret and provisioning URI for enrollment.

Response includes:
- Secret key (for manual entry)
- Provisioning URI (for QR code generation)
- QR code can be displayed to user for easy scanning

### Verify TOTP

**POST /auth/totp/verify**

Confirms the authenticator code and persists it for future logins.

Request body:
```json
{
  "code": "123456"
}
```

After verification, future logins will require the TOTP code along with password.

### Remove TOTP

**DELETE /auth/totp/{id}**

Removes an existing authenticator. Users can have multiple authenticators for backup purposes.

## CSRF Protection

Authenticated POST, PUT, PATCH, and DELETE routes require a CSRF token. After login the API sets a `myportal_session_csrf` cookie containing a random token that must be echoed back via:

- `X-CSRF-Token` header, or
- `_csrf` form field

The cookie is readable by client-side JavaScript so that single-page enhancements can propagate the header automatically.

## Session Management

Sessions use secure, HTTP-only cookies with the following features:

- **Sliding expiration** - Session timeout extends with each request
- **Secure cookie** - Only transmitted over HTTPS in production
- **HTTP-only** - Not accessible via JavaScript (except CSRF token)
- **SameSite protection** - Prevents CSRF attacks

Session secrets are configured via the `SESSION_SECRET` environment variable.

## Rate Limiting

Authentication endpoints are protected by rate limiting to prevent brute force attacks:

- Failed login attempts are tracked per IP address
- Excessive failures trigger temporary blocks
- Can be enhanced with [Fail2ban integration](Fail2ban)

## Security Best Practices

1. **Strong passwords** - Enforce minimum password complexity
2. **Enable TOTP** - Require 2FA for administrative accounts
3. **HTTPS only** - Always use SSL/TLS in production
4. **Monitor failures** - Set up Fail2ban for automated blocking
5. **Regular rotation** - Encourage periodic password changes
6. **Audit logs** - Review authentication events regularly

## Related Documentation

- [Fail2ban Integration](Fail2ban)
- [API Keys](API-Keys)
- [Configuration](Configuration)
- [Setup and Installation](Setup-and-Installation)
