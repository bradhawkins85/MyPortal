# Workflow credential sharing

Both staff workflow directions include **Share MyPortal credential**. The step
accepts `${vars.credential_id}` from **Create MyPortal credential**, or an
existing credential ID; execution always verifies that the credential belongs
to the workflow company.

## Onboarding example

1. Generate password into the protected `generated_password` variable.
2. Create MyPortal credential using `${vars.generated_password}` and output its
   ID as `credential_id`.
3. Share MyPortal credential to a verified `staff` ID or a `job_title`, with a
   reason and only the required permissions (normally `reveal`).
4. Send an email containing `${vars.credential_share.url}`. Do not put the
   generated password in the email body.

The URL for standing staff/title access is an authenticated `/shared-credentials`
portal deep link, not a bearer token. Eligibility is resolved again on every
visit, so a title grant follows an eligible replacement and immediately stops
working for a disabled, departed, unverified, moved, or cross-company staff
member. Standing grants may have an expiry; otherwise they receive an annual
review date.

## Offboarding rotation example

1. Rotate the organisation-controlled account and capture the new password in a
   protected workflow variable.
2. Create MyPortal credential from that variable.
3. Share it to the replacement manager, named staff member, or job title.
4. Notify the recipient with `${vars.credential_share.url}` only.

## External one-time access

Select `external`, provide a recipient email, future UTC expiry, reason,
permissions, and an independently delivered verification code. External links
are bearer URLs, expire at the configured instant, require verification before
use, and can be consumed only once. Anonymous non-expiring links are rejected.
Bearer URLs, verification codes, passwords, and tokens are redacted from step
logs and failure diagnostics.

Workflow execution and step identity form the idempotency boundary for standing
grants, allowing retries and pause/resume to re-resolve the existing grant rather
than issue another. Grant authorization, company membership, expiry, revocation,
and current recipient eligibility are checked again when a portal link is used.
