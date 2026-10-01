# CodeQL alert 2642 disposition

## Disposition

Alert [2642](https://github.com/bradhawkins85/MyPortal/security/code-scanning/2642),
`py/cookie-injection`, is a **false positive** after review. The reported sink
cannot receive an arbitrary request-cookie value. The validation is retained,
and the response-cookie sink now independently enforces the same invariant.

## Source-to-sink evidence

1. `app/services/passkeys.py` defines the accepted value as exactly 43 ASCII
   base64url characters (`A-Z`, `a-z`, `0-9`, `_`, or `-`). Generation uses 32
   random bytes and unpadded URL-safe base64, which produces that format.
2. `app/api/routes/auth.py::_validated_passkey_login_cookie` returns a request
   cookie only after that predicate succeeds.
3. Options generation uses the validated value or a newly generated value. It
   validates generated output before hashing or persistence.
4. `_set_passkey_login_cookie` checks the predicate again immediately before
   `Response.set_cookie`; malformed values raise without producing a header.
5. Verification rejects a missing or malformed binding before challenge lookup.
   The repository consumes a challenge atomically only when it is unconsumed,
   unexpired, and has the matching binding hash. This preserves concurrent
   ceremonies while rejecting mismatches, expiry, and replay.

The focused regression suite inspects serialized `Set-Cookie` headers and covers
missing, valid, oversized, non-ASCII, delimiter-bearing, and control-character
values. It also covers successful login, concurrent ceremonies, mismatch,
expiry, replay, `HttpOnly`, environment-dependent `Secure`, `SameSite=lax`, and
the 300-second TTL.

## Analysis evidence

CodeQL CLI 2.27.0 was run on this tree with the bundled Python
`python-security-extended.qls` suite. It scanned 1,248 Python files without an
analysis error. The query continues to report this sink because its standard
configuration has no built-in cookie-value sanitizers and therefore does not
model the anchored allow-list predicate. No additional passkey-cookie finding
was introduced. This documented, narrowly scoped false-positive disposition is
preferred to removing validation or disabling `py/cookie-injection`.
