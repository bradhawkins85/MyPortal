-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 535: per-user Microsoft 365 single sign-on identities, plus a
-- per-company sso_mode ('off' | 'optional' | 'required').
--
-- A link is keyed on the immutable Entra (tenant id, object id) pair, both
-- stored lower-case. UPN and email are display-only and refreshed on sign-in.
-- provider is 'm365'; linked_via is 'auto' or 'admin'. Timestamps are UTC.
-- The uniqueness constraints are table-level CONSTRAINT ... UNIQUE so the
-- same statement runs on MySQL/MariaDB and on the SQLite fallback.

CREATE TABLE IF NOT EXISTS user_sso_identities (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  provider VARCHAR(32) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  object_id VARCHAR(64) NOT NULL,
  upn VARCHAR(255) NULL,
  email VARCHAR(255) NULL,
  linked_via VARCHAR(16) NOT NULL,
  linked_by_user_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL,
  CONSTRAINT uq_user_sso_identity UNIQUE (provider, tenant_id, object_id),
  CONSTRAINT uq_user_sso_provider UNIQUE (user_id, provider),
  CONSTRAINT fk_user_sso_identities_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_user_sso_identities_linked_by
    FOREIGN KEY (linked_by_user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS sso_mode VARCHAR(16) NOT NULL DEFAULT 'off';
