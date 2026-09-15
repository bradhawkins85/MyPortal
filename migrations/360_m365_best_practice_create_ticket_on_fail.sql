-- Microsoft 365 Best Practices – create-ticket-on-fail setting
--
-- Adds a create_ticket_on_fail column to m365_best_practice_settings so each
-- check can open a company ticket when its result transitions from pass to
-- fail. Initial failures do not create tickets.

ALTER TABLE m365_best_practice_settings
    ADD COLUMN IF NOT EXISTS create_ticket_on_fail TINYINT(1) NOT NULL DEFAULT 0;
