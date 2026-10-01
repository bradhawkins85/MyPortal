# Credential vault security validation and staged rollout

This is the release gate for **ITDOC 18/18**. The vault remains dark by
default: migration 414 creates no enabled company rows. Disabling the UI or a
company flag never deletes credentials, versions, grants, or audit history.

## Threat model and evidence checklist

Use unique sentinel values in staging and retain the test report, not the
sentinels. Exercise internal administrator, technician, ordinary customer,
manager, employee, and impersonated sessions against both the HTML page and API.

| Threat | Required evidence |
|---|---|
| Tenant or direct-ID access | Wrong-company list, reveal, update, grant and revoke requests return 403/404 without metadata. A manager/company admin without an item grant cannot enumerate or reveal. |
| Impersonation | Every create, reveal, rotate, lifecycle and grant operation fails; no decrypt occurs. |
| Guessed/replayed share | Random token/code pairs are indistinguishable from expired links. Two simultaneous consumes produce exactly one success; replay fails. Tokens are stored only as hashes. |
| CSRF and XSS | Browser POSTs without the session CSRF token fail. Labels, usernames, titles and reasons containing HTML render encoded; CSP remains effective. |
| Cache and history | Reveal responses contain `Cache-Control: no-store, private`, `Pragma: no-cache`, and `X-Robots-Tag: noindex, nofollow`; secrets never enter URLs. |
| Logs and telemetry | Search application/proxy logs, audit/event JSON and analytics payloads for every sentinel. Results must be zero; audits contain IDs, outcome and version only. |
| Secondary content | Ordinary search/RAG, ticket and KB HTML/API, notifications/email, company export, PDF/CSV and asset relationship output contain neither sentinel nor ciphertext. |
| Backup | A database-only restore contains ciphertext, nonce and key ID but no sentinel and cannot decrypt without the separately escrowed keyring. |

Also prove repeated standing reveals work while authorised and stop immediately
after grant revocation, staff disablement/departure, job-title change, user
deactivation, membership removal, review expiry, item archive, or item revoke.
Rotation must revoke outstanding one-time grants to the replaced version.

## Staging key-rotation and encrypted-restore rehearsal

1. Create a dedicated staging company and sentinel credential. Take an encrypted
   database backup and separately escrow the current `VAULT_KEYS` value.
2. Add a new 32-byte key under a new immutable ID, retain the old key, change
   `VAULT_ACTIVE_KEY_ID`, and restart. Rotate the sentinel credential.
3. Confirm the new database row names the new key and both versions decrypt only
   with the complete keyring. Confirm dumps, logs and test output lack plaintext.
4. Restore the pre-rotation backup into an isolated database with outbound email,
   webhooks, analytics and integrations disabled. Without keys, confirm a generic
   fail-closed error; then inject the escrowed keyring and reveal the sentinel.
5. Destroy the isolated environment and record operator, timestamps, backup ID,
   key IDs (never key material), checks and results. Retain old keys until every
   backup and immutable version referencing them has passed retention.

Lost keys are not recoverable from the database. Stop reveals, preserve the
database, recover the exact key ID/material through dual-control escrow, and do
not substitute another key. If escrow is unavailable, rotate the credentials at
their source systems and create new encrypted versions.

## Canary and promotion

After migrations and the rehearsal, enable one non-production or approved low-
risk company with a parameterised administrative statement:

```sql
INSERT INTO company_credential_features
  (company_id, enabled, enabled_at, enabled_by_user_id)
VALUES (?, 1, CURRENT_TIMESTAMP, ?)
ON DUPLICATE KEY UPDATE enabled = 1, enabled_at = CURRENT_TIMESTAMP,
  enabled_by_user_id = VALUES(enabled_by_user_id);
```

Record approval outside the SQL transcript; never paste secrets into a terminal.
Run technician creation → manager-approved grant → employee one-time reveal,
expiry/replay/revoke, direct standing reveal, current-title reveal, title change,
offboarding and high-privilege step-up. Probe email, ticket, KB, search, analytics,
audit, logs and exports for the sentinel. Promote companies individually only
after security-owner sign-off and zero leakage results.

## Rollback, compromise and offboarding

Rollback is a flag update (`enabled = 0`), followed by session invalidation and
verification that vault pages/APIs return 404. Do not reverse migrations or
delete encrypted rows. Roll-forward restores access to the same data.

For a compromised share, revoke the grant, invalidate sessions/links, rotate the
upstream credential, store a new version, review value-free audits and notify the
security owner without including the value. During offboarding, deactivate the
user and staff record, remove memberships, revoke direct grants, identify title-
derived grants, rotate every shared/admin value the person could reveal, and
verify old sessions and links fail. MyPortal revocation cannot retract a secret
already seen.
