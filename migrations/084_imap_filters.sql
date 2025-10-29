ALTER TABLE imap_accounts
  ADD COLUMN filter_query TEXT NULL AFTER schedule_cron;
