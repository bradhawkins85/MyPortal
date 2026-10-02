-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 457: Let individual users opt out of AI processing of their
-- requests (Privacy Policy section 4 "Your choices"). The flag defaults to
-- 0 so existing users keep the current behaviour; ai_opt_out_at records when
-- the user (or a super admin acting on their request) last turned it on.
ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_opt_out TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_opt_out_at DATETIME NULL;
