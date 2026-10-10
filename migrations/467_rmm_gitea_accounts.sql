-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 467: Gitea accounts for MyPortal sign-in.
--
-- Technicians with Script editing access sign in to the bundled Gitea through
-- MyPortal. Each row records the Gitea account MyPortal made for a user and
-- the repository access it last granted, so access can be lowered or removed
-- when the user's roles change.

CREATE TABLE IF NOT EXISTS rmm_gitea_accounts (
  user_id INT NOT NULL PRIMARY KEY,
  gitea_login VARCHAR(64) NOT NULL,
  permission VARCHAR(16) NOT NULL DEFAULT 'none',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_rmm_gitea_accounts_login UNIQUE (gitea_login)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
