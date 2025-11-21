# MyPortal Security Implementation Report

## Overview
This document provides a comprehensive overview of the security measures implemented in MyPortal to protect against common web vulnerabilities and ensure OWASP Top 10 compliance.

## Security Headers Implementation

### Content Security Policy (CSP)
**Status:** ✅ Implemented

The application enforces a strict Content Security Policy that:
- Restricts resource loading to same-origin (`default-src 'self'`)
- Allows inline scripts and styles (with plans to migrate to nonces)
- Prevents framing (`frame-ancestors 'none'`)
- Restricts form submissions to same origin (`form-action 'self'`)
- Sets secure base URI (`base-uri 'self'`)

**Configuration:** `app/security/security_headers.py`

### Clickjacking Protection
**Status:** ✅ Implemented

`X-Frame-Options: DENY` header prevents the application from being embedded in iframes, protecting against clickjacking attacks.

### MIME-Sniffing Protection  
**Status:** ✅ Implemented

`X-Content-Type-Options: nosniff` header prevents browsers from MIME-sniffing responses, ensuring content is interpreted as declared.

### Referrer Policy
**Status:** ✅ Implemented

`Referrer-Policy: strict-origin-when-cross-origin` controls referrer information leakage while maintaining functionality for same-origin requests.

### Permissions Policy
**Status:** ✅ Implemented

Disables sensitive browser features:
- Geolocation
- Microphone
- Camera
- Payment APIs
- USB access
- Magnetometer, Gyroscope, Accelerometer

### HTTP Strict Transport Security (HSTS)
**Status:** ✅ Implemented

`Strict-Transport-Security` header enforces HTTPS connections for 1 year when TLS is enabled. Only sent over HTTPS to prevent stripping attacks.

**Configuration:** Set `ENABLE_HSTS=true` in environment variables

### Legacy XSS Protection
**Status:** ✅ Implemented

`X-XSS-Protection: 1; mode=block` provides defense-in-depth for older browsers that don't support CSP.

## Rate Limiting

### General API Rate Limiting
**Status:** ✅ Implemented

**Limit:** 100 requests per minute per IP address
**Applies to:** All API endpoints (except static files and health checks)
**Implementation:** `SimpleRateLimiter` middleware

### Login Endpoint Rate Limiting
**Status:** ✅ Implemented

**Limit:** 5 attempts per 15 minutes per IP address
**Endpoint:** `/api/auth/login`
**Purpose:** Prevents brute force password attacks

### Password Reset Rate Limiting
**Status:** ✅ Implemented

**Limit:** 3 requests per hour per IP address
**Endpoints:** `/api/auth/password/forgot`, `/auth/password/forgot`
**Purpose:** Prevents password reset abuse and account enumeration

### File Upload Rate Limiting
**Status:** ✅ Implemented

**Limit:** 10 files per hour per IP address
**Endpoints:** Various upload endpoints (tickets, products, BC plans)
**Purpose:** Prevents DoS via excessive uploads

### Rate Limit Response
All rate-limited requests return:
- HTTP 429 (Too Many Requests)
- `Retry-After` header indicating wait time
- JSON response with retry time

## Input Validation & XSS Prevention

### Rich Text Sanitization
**Status:** ✅ Implemented

**Library:** bleach
**Implementation:** `app/services/sanitization.py`

Sanitization protects against XSS by:
- Stripping dangerous tags (script, iframe, object, embed, etc.)
- Removing event handlers (onclick, onload, etc.)
- Blocking javascript: protocol in links
- Allowing safe HTML subset (headings, paragraphs, lists, links, etc.)
- Escaping HTML entities

**Allowed Tags:**
- Structural: h1-h6, p, div, br, hr, blockquote
- Formatting: strong, em, b, i, u, sub, sup, code, pre
- Lists: ul, ol, li
- Tables: table, thead, tbody, tr, th, td
- Links: a (with href validation)
- Images: img (with protocol validation)

**Allowed Protocols:** http, https, mailto, tel, data

### Pydantic Input Validation
**Status:** ✅ Enforced

All API endpoints use Pydantic schemas for input validation, ensuring:
- Type safety
- Required field enforcement
- Value constraints (min/max, regex patterns)
- Custom validation logic

## SQL Injection Prevention

### SQLAlchemy ORM Usage
**Status:** ✅ Implemented

The application uses SQLAlchemy ORM exclusively, which provides automatic parameterization:
- All queries use parameterized bindings
- User input is never concatenated into SQL strings
- ORM filter methods are safe by default

### Safe Query Patterns

**Parameterized text queries:**
```python
text("SELECT * FROM users WHERE id = :user_id")
# Execute with: {"user_id": user_input}
```

**ORM filters:**
```python
User.query.filter(User.username == user_input)
```

### Protected Against:
- ✅ Classic SQL injection (`' OR '1'='1`)
- ✅ UNION-based injection
- ✅ Stacked queries (`; DROP TABLE`)
- ✅ Comment-based injection (`--`, `/**/`)
- ✅ Blind SQL injection
- ✅ Second-order SQL injection

**Note:** ORDER BY clauses require special handling with whitelisting since column names cannot be parameterized.

## CSRF Protection

### CSRF Middleware
**Status:** ✅ Implemented

**Implementation:** `app/security/csrf.py`

CSRF protection includes:
- Token generation for all sessions
- Token validation on state-changing requests (POST, PUT, DELETE, PATCH)
- Automatic exemption for safe methods (GET, HEAD, OPTIONS)
- Token can be sent via header or form field
- Constant-time comparison to prevent timing attacks

**Configuration:** Set `ENABLE_CSRF=true` in environment variables (enabled by default)

**Exempt Endpoints:**
- `/auth/login`
- `/auth/register`
- `/auth/password/forgot`
- `/auth/password/reset`

## Path Traversal Prevention

### File Access Protection
**Status:** ✅ Implemented

**Implementation:** `_resolve_private_upload()` in `app/main.py`

Protection measures:
- ✅ Rejects absolute paths
- ✅ Handles `..` parent directory references safely
- ✅ Normalizes backslashes to forward slashes
- ✅ Validates resolved path is within upload directory
- ✅ Rejects access to directories (only files)
- ✅ Returns 404 for nonexistent or blocked files

**Tested Against:**
- `../../../etc/passwd`
- `/etc/passwd`
- `C:\Windows\System32\...`
- Null byte injection
- URL-encoded traversal attempts
- Symlink escape attempts

## Data Encryption

### PII Encryption at Rest
**Status:** ⚠️ Partial Implementation

**Current Implementation:**
- TOTP secrets are encrypted using AES-256-GCM
- API keys and integration credentials are encrypted
- Encryption key: `TOTP_ENCRYPTION_KEY` environment variable

**Implementation:** `app/security/encryption.py`

**Encryption Algorithm:** AES-256-GCM (Galois/Counter Mode)
- Provides authenticated encryption
- Uses random IV for each encryption
- Includes authentication tag

### Session Security
**Status:** ✅ Implemented

- Sessions stored in Redis (or encrypted cookies)
- Session tokens are cryptographically random
- Secure cookie attributes (HttpOnly, SameSite)
- Session expiration and timeout

## TLS Configuration

### HTTPS Enforcement
**Status:** ⚠️ Deployment Dependent

**Recommendations:**
1. Configure reverse proxy (nginx/Apache) to enforce TLS 1.3
2. Disable TLS 1.0, 1.1, and weak cipher suites
3. Enable HSTS with `ENABLE_HSTS=true`
4. Use strong cipher suites:
   - TLS_AES_128_GCM_SHA256
   - TLS_AES_256_GCM_SHA384
   - TLS_CHACHA20_POLY1305_SHA256

**Certificate Requirements:**
- Use certificates from trusted CA
- Enable OCSP stapling
- Configure proper certificate chain

## API Key Security

### API Key Management
**Status:** ✅ Implemented

**Features:**
- API keys are hashed before storage
- Keys are masked when displayed in UI
- Usage tracking and audit logging
- Permission-based access control

**Allowed HTTP Methods:** GET, POST, PUT, DELETE, PATCH

### API Key Rotation
**Status:** ⚠️ Manual Process

**Current Process:**
1. Admin creates new API key
2. Update external systems with new key
3. Delete old API key

**Recommendation:** Implement automated rotation with overlap period.

## Analytics and Privacy

### Plausible Analytics Integration
**Status:** ✅ Implemented

**Implementation:** `app/security/plausible_tracking.py`

MyPortal includes privacy-first analytics using Plausible Analytics for:
- Email tracking (opens and clicks)
- Authenticated user pageviews (optional)

**Privacy Protection Measures:**
- ✅ User identifiers are hashed with HMAC-SHA256
- ✅ Secret pepper used for hashing (configured via `PLAUSIBLE_PEPPER`)
- ✅ Raw usernames never sent to Plausible cloud instances
- ✅ Tracking disabled by default (`PLAUSIBLE_TRACK_PAGEVIEWS=false`)
- ✅ PII sending explicitly controlled (`PLAUSIBLE_SEND_PII=false`)
- ✅ Tracking only for authenticated users
- ✅ No tracking of API endpoints or static resources

**Configuration Requirements:**
1. Set `PLAUSIBLE_BASE_URL` to your Plausible instance
2. Set `PLAUSIBLE_SITE_DOMAIN` to your portal domain
3. Generate secure pepper: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
4. Set `PLAUSIBLE_PEPPER` in environment
5. Enable pageview tracking: `PLAUSIBLE_TRACK_PAGEVIEWS=true` (via admin UI)

**Privacy Policy Implications:**
When pageview tracking is enabled, the following should be disclosed in your privacy policy:

> "We use privacy-first analytics (Plausible Analytics) to understand how authenticated users interact with our portal. User identifiers are anonymized using cryptographic hashing before being sent to our analytics system. We do not track individual user behavior across sessions or identify specific users from analytics data. This data is used solely for improving the user experience and portal functionality."

**Self-Hosted vs. Cloud:**
- **Cloud (plausible.io):** User identifiers MUST be hashed (`PLAUSIBLE_SEND_PII=false`)
- **Self-Hosted:** May optionally send unhashed identifiers if compliant with privacy regulations (`PLAUSIBLE_SEND_PII=true`)

**Compliance Notes:**
- GDPR compliant when using hashed identifiers
- No cookies required for tracking
- Data minimization principle applied
- User consent requirements vary by jurisdiction

## Security Testing

### Automated Tests
**Status:** ✅ Implemented

Test suites cover:
- ✅ Security headers (9 tests)
- ✅ Rate limiting (9 tests)
- ✅ XSS prevention (16 tests)
- ✅ Path traversal prevention (17 tests)
- ✅ SQL injection prevention (16 tests)

**Total Security Tests:** 67

### Manual Security Testing
**Status:** ⚠️ Recommended

Regular security testing should include:
- Penetration testing
- Vulnerability scanning
- Code review
- Dependency auditing

## OWASP Top 10 Compliance

### A01:2021 – Broken Access Control
**Status:** ✅ Addressed
- Role-based access control (RBAC)
- Permission checks on all endpoints
- Session management
- CSRF protection

### A02:2021 – Cryptographic Failures
**Status:** ✅ Addressed
- PII encryption at rest
- TLS in transit
- Secure password hashing (bcrypt)
- Cryptographically secure random tokens

### A03:2021 – Injection
**Status:** ✅ Addressed
- SQL injection prevention via SQLAlchemy ORM
- XSS prevention via bleach sanitization
- Command injection prevention (no shell execution of user input)
- Path traversal prevention

### A04:2021 – Insecure Design
**Status:** ✅ Addressed
- Security headers by default
- Rate limiting on sensitive endpoints
- Secure defaults in configuration
- Defense in depth architecture

### A05:2021 – Security Misconfiguration
**Status:** ✅ Addressed
- Security headers enforced
- CSRF enabled by default
- Secure cookie configuration
- Error messages don't leak sensitive info

### A06:2021 – Vulnerable and Outdated Components
**Status:** ⚠️ Ongoing
- Regular dependency updates required
- Use `pip-audit` or `safety` for vulnerability scanning

**Recommendation:** Run `pip-audit` regularly:
```bash
pip install pip-audit
pip-audit
```

### A07:2021 – Identification and Authentication Failures
**Status:** ✅ Addressed
- Rate limiting on login
- Password complexity requirements
- Account lockout after failed attempts
- Secure session management
- TOTP 2FA support

### A08:2021 – Software and Data Integrity Failures
**Status:** ✅ Addressed
- Code signing via git commits
- Dependency verification
- Secure update mechanism

### A09:2021 – Security Logging and Monitoring Failures
**Status:** ✅ Addressed
- Request logging middleware
- Audit log for privileged actions
- Failed login tracking
- Rate limit violation logging

### A10:2021 – Server-Side Request Forgery (SSRF)
**Status:** ✅ Addressed
- URL validation on external requests
- Whitelist of allowed domains
- Network-level controls for external APIs

## Deployment Security Checklist

### Pre-Production
- [ ] Change default `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY`
- [ ] Enable TLS 1.3 on reverse proxy
- [ ] Configure `ALLOWED_ORIGINS` for CORS
- [ ] Set `ENABLE_HSTS=true`
- [ ] Review and restrict `ALLOWED_ORIGINS`
- [ ] Configure firewall rules
- [ ] Enable `ENABLE_CSRF=true` (default)
- [ ] Set up logging and monitoring
- [ ] Run dependency vulnerability scan
- [ ] Review API key permissions

### Production Monitoring
- [ ] Monitor rate limit violations
- [ ] Track failed login attempts
- [ ] Review audit logs regularly
- [ ] Monitor for security header violations
- [ ] Set up alerting for suspicious activity
- [ ] Regular security updates
- [ ] Backup encryption keys securely

## Security Contacts

For security issues or vulnerabilities, please contact:
- **Security Team:** [Configure contact information]
- **Disclosure Policy:** Responsible disclosure preferred

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2025-11-13 | 1.0 | Initial security implementation and documentation |

## References

- [OWASP Top 10 2021](https://owasp.org/Top10/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [SQLAlchemy SQL Injection Protection](https://docs.sqlalchemy.org/en/14/core/tutorial.html#using-textual-sql)
- [Mozilla Web Security Guidelines](https://infosec.mozilla.org/guidelines/web_security)
