# Passkeys in MyPortal

MyPortal supports optional passkeys for existing accounts. A passkey can use a device fingerprint reader, facial recognition, device PIN, or a compatible security key.

## User guidance

- Register passkeys from **My profile → Account security → Passkeys**.
- MyPortal requires your current password before adding or removing a passkey.
- You can register multiple passkeys and rename them later.
- Use **Sign in with a passkey** on the login page to authenticate without entering your email address or MyPortal password.
- If a passkey is unavailable, continue using your existing password sign-in and recovery options.

## MFA treatment

MyPortal requires **user-verified** passkeys. A successful passkey authentication is treated as satisfying the existing interactive MFA sign-in requirement because the authenticator must verify the user during the WebAuthn ceremony.

Accounts that still need to complete MyPortal's required TOTP enrolment are redirected to the normal enrolment flow after sign-in, matching the existing session flow.

## Removal and recovery

Removing a passkey blocks future MyPortal sign-ins with that credential, but it may still appear in the user's device or password manager until removed there as well.

Removing a passkey **does not revoke existing sessions** that were already created with it. Existing sessions continue until they expire or the user signs out.

The final passkey can be removed as long as another permitted sign-in or recovery method remains available, such as the account password plus the standard password-reset flow.

## Configuration

Set the following environment variables:

- `PASSKEY_RP_ID` - optional explicit relying party ID. If blank, MyPortal uses the host from `PORTAL_URL`.
- `PASSKEY_RP_NAME` - optional relying party display name. If blank, MyPortal uses `APP_NAME`.
- `PASSKEY_ALLOWED_ORIGINS` - comma-separated list of exact allowed origins for passkey ceremonies.

## HTTPS and reverse proxies

- Production passkeys require HTTPS.
- For local development, `https://localhost:8000` or `http://localhost:8000` can be used while testing on a local machine.
- When MyPortal is behind a reverse proxy, `PORTAL_URL` and `PASSKEY_ALLOWED_ORIGINS` must match the public HTTPS origin shown to browsers. Do not rely on untrusted request headers to derive passkey origins.

## Supported browsers and devices

Initial support depends on browser and operating-system WebAuthn support. Representative combinations to validate manually include:

- Windows with Edge or Chrome
- macOS with Safari or Chrome
- iPhone/iPad with Safari
- Android with Chrome
- A compatible FIDO2 security key on desktop or mobile

## Current limitations

- The first release provides an explicit **Sign in with a passkey** button rather than conditional autofill.
- MyPortal never receives or stores private keys or biometric data. It stores credential metadata, public keys, counters, and usage timestamps only.
