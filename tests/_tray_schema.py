"""SQLite schema shared by the tray test modules.

The SQLite migration adapter cannot run every MySQL migration, so tray tests
build the tables they need with SQLite-native DDL that mirrors
``migrations/235_tray_app.sql`` and the later ``tray_devices`` column
migrations. Keep this in step with those migrations.
"""

TRAY_SQLITE_DDL = [
    """CREATE TABLE IF NOT EXISTS migrations (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           name TEXT NOT NULL UNIQUE,
           applied_at TEXT DEFAULT (datetime('now'))
       )""",
    """INSERT OR IGNORE INTO migrations (name)
           VALUES ('235_tray_app.sql')""",
    """CREATE TABLE IF NOT EXISTS chat_rooms (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           tray_device_id INTEGER NULL
       )""",
    """CREATE TABLE IF NOT EXISTS companies (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NULL,
          tray_chat_enabled INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS tray_install_tokens (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           company_id INTEGER NULL,
           label TEXT NOT NULL,
           token_hash TEXT NOT NULL UNIQUE,
           token_prefix TEXT NOT NULL,
           created_by_user_id INTEGER NULL,
           created_at TEXT DEFAULT (datetime('now')),
           expires_at TEXT NULL,
           revoked_at TEXT NULL,
           last_used_at TEXT NULL,
           use_count INTEGER NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS tray_devices (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           company_id INTEGER NULL,
           asset_id INTEGER NULL,
           device_uid TEXT NOT NULL UNIQUE,
           enrolment_token_id INTEGER NULL,
           auth_token_hash TEXT NOT NULL,
           auth_token_prefix TEXT NOT NULL,
           os TEXT NULL,
           os_version TEXT NULL,
           hostname TEXT NULL,
           serial_number TEXT NULL,
           agent_version TEXT NULL,
           console_user TEXT NULL,
           last_ip TEXT NULL,
           last_seen_utc TEXT NULL,
           status TEXT NOT NULL DEFAULT 'pending',
           network_scanner_enabled INTEGER NOT NULL DEFAULT 0,
           network_scan_interval_minutes INTEGER NOT NULL DEFAULT 360,
           network_scan_wan_cidrs TEXT NULL,
           network_scan_local_cidrs TEXT NULL,
           ws_last_seen_utc TEXT NULL,
           setup_ticket_processed INTEGER NOT NULL DEFAULT 0,
           overseer_version TEXT NULL,
           overseer_last_seen_utc TEXT NULL,
           ui_token_hash TEXT NULL,
           ui_token_prefix TEXT NULL,
           public_key TEXT NULL,
           public_key_registered_at TEXT NULL,
           console_user_active INTEGER NULL,
           created_at TEXT DEFAULT (datetime('now')),
           updated_at TEXT DEFAULT (datetime('now'))
       )""",
    """CREATE TABLE IF NOT EXISTS tray_menu_configs (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           name TEXT NOT NULL,
           scope TEXT NOT NULL DEFAULT 'global',
           scope_ref_id INTEGER NULL,
           payload_json TEXT NOT NULL,
           display_text TEXT NULL,
           env_allowlist TEXT NULL,
           branding_icon_url TEXT NULL,
           enabled INTEGER NOT NULL DEFAULT 1,
           version INTEGER NOT NULL DEFAULT 1,
           created_by_user_id INTEGER NULL,
           updated_by_user_id INTEGER NULL,
           created_at TEXT DEFAULT (datetime('now')),
           updated_at TEXT DEFAULT (datetime('now')),
           target_rules TEXT NULL,
           priority INTEGER NOT NULL DEFAULT 100
       )""",
    """CREATE TABLE IF NOT EXISTS tray_command_log (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           device_id INTEGER NOT NULL,
           command TEXT NOT NULL,
           payload_json TEXT NULL,
           initiated_by_user_id INTEGER NULL,
           status TEXT NOT NULL DEFAULT 'queued',
           error TEXT NULL,
           created_at TEXT DEFAULT (datetime('now')),
           delivered_at TEXT NULL
       )""",
    # Phase 5 tables
    """CREATE TABLE IF NOT EXISTS tray_diagnostics (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           device_id INTEGER NOT NULL,
           filename TEXT NOT NULL,
           content_type TEXT NOT NULL DEFAULT 'application/zip',
           size_bytes INTEGER NOT NULL DEFAULT 0,
           stored_path TEXT NOT NULL,
           uploaded_at TEXT DEFAULT (datetime('now')),
           reviewed_by_user_id INTEGER NULL,
           reviewed_at TEXT NULL,
           notes TEXT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS tray_versions (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           version TEXT NOT NULL,
           platform TEXT NOT NULL DEFAULT 'all',
           download_url TEXT NOT NULL,
           required INTEGER NOT NULL DEFAULT 0,
           enabled INTEGER NOT NULL DEFAULT 1,
           release_notes TEXT NULL,
           published_by_user_id INTEGER NULL,
           published_at TEXT DEFAULT (datetime('now')),
           rollout_percent INTEGER NOT NULL DEFAULT 100,
           rollout_start_at TEXT NULL,
           sha256 TEXT NULL
       )""",
]
