-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 522: the least severe event level RMM agents send.
--
-- rmm_event_log_settings.max_level is the least severe level a company
-- (scope_key 'company:<id>') or one device ('asset:<id>') keeps: 1 critical,
-- 2 error, 3 warning, 4 information, 5 verbose (everything). NULL uses the
-- company's choice, then EVENT_LOG_MIN_LEVEL. 'Log always' events (level 0,
-- such as Security audits) are always kept.

ALTER TABLE rmm_event_log_settings ADD COLUMN IF NOT EXISTS max_level TINYINT NULL;
