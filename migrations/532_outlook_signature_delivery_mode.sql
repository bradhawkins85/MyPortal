-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- How MyPortal signatures reach Outlook for each company: pushed through
-- Exchange Online with roaming signatures postponed ('exchange'), or inserted
-- by the MyPortal Outlook add-in with roaming signatures left on ('addin').
-- outlook_addin_id is the stable add-in manifest id generated for the company.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS signature_delivery_mode VARCHAR(16) NOT NULL DEFAULT 'exchange';
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS outlook_addin_id CHAR(36) NULL;
