-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- When each Software name was last looked up in Chocolatey. The refresh saves
-- its progress as it goes and starts with the names checked longest ago, so a
-- run interrupted by a restart or the feed's rate limit resumes next time.
CREATE TABLE IF NOT EXISTS rmm_chocolatey_lookups (
  name VARCHAR(255) NOT NULL PRIMARY KEY,
  checked_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
