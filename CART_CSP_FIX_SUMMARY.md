# Cart Page CSP Violations Fix - Summary

## Problem Statement

Users were experiencing Content Security Policy (CSP) violations when attempting to use forms on the `/cart` page. The browser console displayed the following errors:

```
cart:1  Sending form data to 'https://portal.hawkinsit.au/cart/update' violates the following Content Security Policy directive: "form-action 'self'". The request has been blocked.

cart:1  Sending form data to 'https://portal.hawkinsit.au/cart/remove' violates the following Content Security Policy directive: "form-action 'self'". The request has been blocked.

cart:1  Sending form data to 'https://portal.hawkinsit.au/cart/place-order' violates the following Content Security Policy directive: "form-action 'self'". The request has been blocked.
```

These errors prevented users from:
- Updating cart quantities
- Removing items from the cart
- Placing orders

## Root Cause Analysis

The issue was traced to the CSP validation logic in `app/security/security_headers.py`. Specifically:

1. The `_is_valid_csp_source()` method only accepted HTTPS URLs (line 174: `if not source.startswith("https://")`)
2. The `PORTAL_URL` environment variable could be set to an HTTP URL (as shown in `.env.example`: `PORTAL_URL=http://localhost:8000`)
3. When a non-HTTPS portal URL was configured, it failed validation and wasn't added to the CSP `form-action` directive
4. The resulting CSP header only contained `form-action 'self'` without the portal URL as an allowed submission target

This caused form submissions to be blocked in scenarios where:
- The application was accessed via a different origin than the configured portal URL
- There was a protocol mismatch (HTTP vs HTTPS) between the page and form submission
- The application was behind a reverse proxy with SSL termination

## Solution Implemented

### Code Changes

Modified `app/security/security_headers.py`, line 173-175:

**Before:**
```python
# Must be HTTPS URL
if not source.startswith("https://"):
    return False
```

**After:**
```python
# Must be HTTP or HTTPS URL
if not (source.startswith("https://") or source.startswith("http://")):
    return False
```

### Why This Fix Works

1. **Allows HTTP URLs**: Development and certain deployment configurations can use HTTP URLs for the portal
2. **Maintains Security**: The validation still prevents CSP injection by checking for special characters and validating URL format
3. **Enables Proper CSP Configuration**: The portal URL is now consistently added to the CSP `form-action` directive, resulting in headers like:
   ```
   form-action 'self' https://portal.hawkinsit.au
   ```
   or
   ```
   form-action 'self' http://localhost:8000
   ```

4. **Supports All Environments**: Works correctly in:
   - Development (HTTP)
   - Staging/Production (HTTPS)
   - Behind reverse proxies (SSL termination)

## Testing

### Unit Tests
Added comprehensive tests in `tests/test_csp_form_action.py`:
- ✅ Validates both HTTP and HTTPS URLs are accepted
- ✅ Confirms portal URLs are included in CSP headers
- ✅ Verifies invalid URLs are still rejected
- ✅ Tests CSP generation with both HTTP and HTTPS portal URLs

### Regression Testing
- ✅ All existing cart tests pass (`test_cart_update.py`, `test_shop_cart_permission_visibility.py`)
- ✅ No security vulnerabilities detected by CodeQL
- ✅ No breaking changes to existing functionality

## Security Considerations

### Security Impact: LOW
- The change maintains the same level of security protection
- URL validation still prevents injection attacks
- Only valid HTTP/HTTPS URLs with proper domain format are accepted
- CSP continues to restrict form submissions to approved origins

### Why Allowing HTTP is Safe
1. The CSP source validation still checks for injection attempts (special characters, malformed URLs)
2. The portal URL is configured by the system administrator, not user input
3. HTTP URLs are only relevant for development environments
4. Production deployments should use HTTPS (and the fix supports both)

## Deployment Notes

### Environment Configuration
Ensure the `PORTAL_URL` environment variable is set correctly:

**Development:**
```bash
PORTAL_URL=http://localhost:8000
```

**Production:**
```bash
PORTAL_URL=https://portal.hawkinsit.au
```

### Verification Steps
After deployment:
1. Navigate to the `/cart` page
2. Open browser developer tools (Console tab)
3. Attempt to update cart quantities, remove items, or place an order
4. Verify no CSP violation errors appear
5. Check that forms submit successfully

### Rollback Plan
If issues arise, revert commit `65b03f9` which will restore the original HTTPS-only validation. However, this will reintroduce the original bug for HTTP configurations.

## Files Changed

1. `app/security/security_headers.py` - Modified CSP URL validation
2. `tests/test_csp_form_action.py` - Added comprehensive test coverage

## Conclusion

This fix resolves the CSP violations on the cart page by allowing both HTTP and HTTPS portal URLs to be included in the CSP `form-action` directive. The change is minimal, secure, and fully tested. It ensures cart functionality works correctly across all deployment environments while maintaining the security protections provided by CSP.
