-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 555: Asset table columns that admins have hidden from end users.
-- A row's column_key is a built-in column key (e.g. health_score) or cf_<id>
-- for an asset custom field. Super admins and helpdesk technicians still see
-- every column.
CREATE TABLE IF NOT EXISTS asset_column_restrictions (
    column_key VARCHAR(64) NOT NULL PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
