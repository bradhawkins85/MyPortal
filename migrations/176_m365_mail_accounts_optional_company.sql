-- Make company_id optional on m365_mail_accounts so mailbox rules can
-- resolve the ticket company from the sender's email domain instead of
-- requiring a fixed company link.

ALTER TABLE m365_mail_accounts MODIFY company_id INT NULL;
