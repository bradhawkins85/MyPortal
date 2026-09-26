# Credential vault operations

The credential vault uses AES-256-GCM with associated data binding every value
to its company, credential and immutable version. It deliberately does **not**
reuse `TOTP_ENCRYPTION_KEY`; existing TOTP ciphertext and key rotation remain
unchanged.

## Key custody and access

Generate a key with `openssl rand -base64 32`. Store it in the production
secret manager or a root-owned (`0600`) environment file as
`VAULT_KEYS=2026-01:<base64>` and set `VAULT_ACTIVE_KEY_ID=2026-01`. The root
key must never be stored in MySQL, SQLite, source control, application logs,
database dumps, analytics or tracing. Only the application service identity
and the documented recovery custodians may read it.

Back up the keyring separately using the organisation's encrypted, offline
secret backup and dual-control process. A database backup is intentionally
unreadable without that keyring. Test restores must restore the database and
the exact key IDs into an isolated environment, reveal one designated test
credential, and then destroy the environment.

## Rotation rehearsal

1. Add a new key to `VAULT_KEYS` while retaining every old key and restart.
2. Change `VAULT_ACTIVE_KEY_ID`; new versions now use the new key.
3. Create a new secret version for a test credential. Confirm both its old
   version (repository-level recovery check) and current version decrypt.
4. Re-encrypt production credentials by creating new immutable versions.
5. Retain old keys for the backup-retention window. Remove one only after all
   retained backups and versions that reference it have expired.

If a key is lost, ciphertext using it cannot be recovered by design. Restore
the key from the separate escrow backup; do not reset key IDs or substitute a
new key. Authentication failure is reported generically and fails closed.

## Backup-safe rollback

Migration 410 is additive. During rollback, deploy the earlier application but
leave the three vault tables intact; older code ignores them and secrets remain
readable after roll-forward. Never drop these tables as an automated down
migration. After an independently verified encrypted backup and an authorised
plaintext migration/export (if required), operators may remove them manually.

List/search and normal exports select credential metadata only. Plaintext is
returned solely by the authorised POST reveal endpoint with `no-store` and
`noindex` headers. Audit events contain IDs, company and version only. Secret
request models use `SecretStr`, and ciphertext columns are absent from response
models, audit diffs, search indexes, hydration payloads, and CSV/PDF exports.
