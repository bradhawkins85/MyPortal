-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 455: Link Spam Search & Purge requests to the Microsoft Defender
-- "Email reported by user as ..." alert they were created from, so the
-- Reported Emails page shows which alerts already have a search.
ALTER TABLE m365_spam_purge_requests ADD COLUMN IF NOT EXISTS source_alert_id VARCHAR(191) NULL;
CREATE INDEX IF NOT EXISTS idx_m365_spam_purge_source_alert
    ON m365_spam_purge_requests (company_id, source_alert_id);
