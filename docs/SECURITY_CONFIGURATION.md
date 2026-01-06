# Security Configuration Guide

This guide covers the security features available in MyPortal, including CORS configuration and IP whitelisting.

## CORS (Cross-Origin Resource Sharing)

MyPortal implements a secure-by-default CORS policy that protects your application from unauthorized cross-origin requests.

### Default Behavior

By default, MyPortal only allows same-origin requests. This means:
- Requests from the same domain and port are allowed
- Cross-origin requests from other domains are blocked
- API requests from web browsers on different domains cannot access your MyPortal instance

### Configuring Allowed Origins

To allow specific domains to make cross-origin requests to your MyPortal instance, configure the `ALLOWED_ORIGINS` environment variable:

```bash
# Allow a single origin
ALLOWED_ORIGINS=https://app.example.com

# Allow multiple origins (comma-separated)
ALLOWED_ORIGINS=https://app.example.com,https://dashboard.example.com

# For development only (NOT for production)
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080
```

### Security Best Practices

1. **Never use wildcard (`*`) origins in production** - This allows any website to access your API
2. **Use HTTPS origins only** - HTTP origins should only be used for local development
3. **Be specific** - Only add the exact origins that need access to your API
4. **Review regularly** - Periodically audit your allowed origins to remove unused ones

### CORS Headers

MyPortal automatically configures the following CORS headers:

- `Access-Control-Allow-Origin`: Restricted to configured origins (or same-origin only)
- `Access-Control-Allow-Credentials`: `true` (allows authenticated requests)
- `Access-Control-Allow-Methods`: `GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD`
- `Access-Control-Allow-Headers`: `*` (allows all request headers)

## IP Whitelisting

IP whitelisting provides an additional layer of security by restricting access to sensitive endpoints based on the client's IP address.

### Enabling IP Whitelisting

To enable IP whitelisting, set the following environment variables:

```bash
# Enable IP whitelisting
IP_WHITELIST_ENABLED=true

# Define allowed IP addresses or CIDR ranges (comma-separated)
IP_WHITELIST=192.168.1.0/24,10.0.0.5,2001:db8::/32

# Apply to admin paths only (default) or all API paths
IP_WHITELIST_ADMIN_ONLY=true
```

### IP Whitelist Configuration

#### Individual IP Addresses

Allow specific IP addresses:

```bash
IP_WHITELIST=192.168.1.100,10.0.0.50
```

#### CIDR Ranges

Allow ranges of IP addresses using CIDR notation:

```bash
# Allow all IPs in the 192.168.1.0/24 subnet
IP_WHITELIST=192.168.1.0/24

# Allow multiple ranges
IP_WHITELIST=192.168.1.0/24,10.0.0.0/8,172.16.0.0/12
```

#### IPv6 Support

IP whitelisting fully supports IPv6 addresses and ranges:

```bash
IP_WHITELIST=2001:db8::/32,2001:db8::1
```

### Protected Paths

By default, IP whitelisting protects the following paths:

- `/admin/*` - All admin routes
- `/api/*` - All API routes (only if `IP_WHITELIST_ADMIN_ONLY=false`)

### Exempt Paths

The following paths are always exempt from IP whitelisting:

- `/static/*` - Static files
- `/health` - Health check endpoint
- `/login` - Login page
- `/register` - Registration page
- `/api/auth/login` - API login endpoint
- `/api/auth/register` - API registration endpoint
- `/api/webhooks/*` - Webhook endpoints (use signature verification instead)
- `/manifest.webmanifest` - PWA manifest
- `/service-worker.js` - Service worker script

### Proxy Headers

IP whitelisting respects the following proxy headers to determine the client's real IP address:

1. `CF-Connecting-IP` (Cloudflare)
2. `X-Forwarded-For` (standard proxy header)
3. Direct client IP (fallback)

### Security Considerations

1. **Use with API keys** - IP whitelisting works alongside API key authentication for defense in depth
2. **Dynamic IPs** - Consider CIDR ranges if your users have dynamic IP addresses
3. **VPN/Proxy** - Account for VPNs and proxies when configuring allowed IPs
4. **Logging** - Failed IP whitelist checks are logged for security monitoring
5. **Multiple layers** - IP whitelisting should complement, not replace, authentication

## Example Configurations

### Development Environment

```bash
# Allow same-origin requests only (no CORS)
ALLOWED_ORIGINS=

# Disable IP whitelisting for easier development
IP_WHITELIST_ENABLED=false
```

### Production Environment (Single Server)

```bash
# Only allow requests from your frontend domain
ALLOWED_ORIGINS=https://portal.example.com

# Restrict admin access to office IP range
IP_WHITELIST_ENABLED=true
IP_WHITELIST=203.0.113.0/24
IP_WHITELIST_ADMIN_ONLY=true
```

### Production Environment (Multiple Servers)

```bash
# Allow requests from multiple frontend applications
ALLOWED_ORIGINS=https://portal.example.com,https://app.example.com,https://mobile.example.com

# Restrict admin access to office and VPN IP ranges
IP_WHITELIST_ENABLED=true
IP_WHITELIST=203.0.113.0/24,198.51.100.0/24
IP_WHITELIST_ADMIN_ONLY=true
```

### High-Security Environment

```bash
# No cross-origin requests allowed
ALLOWED_ORIGINS=

# Restrict ALL access to specific IP ranges
IP_WHITELIST_ENABLED=true
IP_WHITELIST=10.0.0.0/8
IP_WHITELIST_ADMIN_ONLY=false
```

## Troubleshooting

### CORS Errors in Browser Console

If you see CORS errors like "Access to fetch at '...' from origin '...' has been blocked by CORS policy":

1. Check that the origin is included in `ALLOWED_ORIGINS`
2. Verify the origin format matches exactly (including protocol and port)
3. Ensure there are no trailing slashes in the origin URLs
4. Check server logs for CORS-related warnings

### IP Whitelist Blocks Legitimate Users

If legitimate users are being blocked by IP whitelist:

1. Check server logs for the blocked IP address
2. Verify the IP is included in your whitelist or CIDR range
3. Consider if the user is behind a proxy or VPN
4. Check if proxy headers are being forwarded correctly
5. Use a broader CIDR range if users have dynamic IPs

### Webhook Signature Failures

Webhooks are exempt from IP whitelisting because they use signature verification instead. If webhook signature verification fails:

1. Verify the webhook secret is configured correctly
2. Check that the webhook provider supports the signature format
3. Review webhook monitor logs for detailed signature information
4. Temporarily disable signature verification for testing (not recommended for production)

## Migration Guide

### Upgrading from Wildcard CORS

If you previously used wildcard CORS (`ALLOWED_ORIGINS=*`):

1. Identify all domains that need to access your API
2. Configure each domain explicitly in `ALLOWED_ORIGINS`
3. Test cross-origin requests from each domain
4. Remove the wildcard configuration

### Adding IP Whitelisting to Existing Deployment

1. Start with `IP_WHITELIST_ADMIN_ONLY=true` to only protect admin routes
2. Add your current IP address to the whitelist for testing
3. Enable IP whitelisting: `IP_WHITELIST_ENABLED=true`
4. Test admin access to ensure you're not locked out
5. Gradually expand the whitelist to include all authorized IPs
6. Consider setting `IP_WHITELIST_ADMIN_ONLY=false` for maximum security

## Security Monitoring

MyPortal logs the following security events:

- CORS policy violations (unauthorized origins)
- IP whitelist blocks (unauthorized IP addresses)
- Wildcard CORS configuration warnings
- IP whitelist configuration changes

Review these logs regularly to identify:
- Unauthorized access attempts
- Misconfigured clients
- Missing whitelist entries
- Potential security threats

## Related Security Features

MyPortal includes several other security features that work alongside CORS and IP whitelisting:

- **API Keys with IP restrictions** - API keys can have their own IP restrictions
- **Rate limiting** - Protects against brute force and DoS attacks
- **CSRF protection** - Prevents cross-site request forgery
- **Security headers** - Content-Security-Policy, X-Frame-Options, etc.
- **Request logging** - Audit trail of all API requests
- **Webhook signature verification** - Validates incoming webhook payloads

## Support

For additional help with security configuration:

1. Review the `.env.example` file for complete configuration examples
2. Check the security section in the main README
3. Consult the MyPortal documentation wiki
4. Open an issue on GitHub for security-related questions
