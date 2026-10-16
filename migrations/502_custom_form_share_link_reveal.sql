-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false

-- Encrypted copy of each share token so admins can reveal links and QR codes
-- after creation. Links created before this column existed stay hash-only.
ALTER TABLE custom_form_share_links ADD COLUMN token_encrypted TEXT NULL;
