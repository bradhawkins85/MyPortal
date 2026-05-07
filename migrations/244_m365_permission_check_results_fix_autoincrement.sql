-- Fix m365_permission_check_results.id missing AUTO_INCREMENT on MySQL.
--
-- Migration 243 recreated this table using INTEGER PRIMARY KEY without
-- AUTO_INCREMENT.  In SQLite that is fine because INTEGER PRIMARY KEY is
-- implicitly an alias for ROWID and auto-assigns values; in MySQL it is NOT
-- – rows inserted without an explicit id value raise:
--
--   (1364, "Field 'id' doesn't have a default value")
--
-- This migration restores AUTO_INCREMENT on the id column for MySQL.
-- The ALTER TABLE … MODIFY COLUMN statement is not supported by SQLite;
-- the migration runner silently continues past unsupported statements when
-- using the SQLite backend, so no separate SQLite handling is required.

ALTER TABLE m365_permission_check_results
    MODIFY COLUMN id INTEGER NOT NULL AUTO_INCREMENT;
