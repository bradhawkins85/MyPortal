-- Registry of external plugins and their enabled state.
CREATE TABLE IF NOT EXISTS plugin_registry (
  slug VARCHAR(255) PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  installed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
