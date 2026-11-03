-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 539: per-company RustDesk unattended access.
--
-- Some customers want to approve every remote desktop session on their own
-- computers. With rustdesk_unattended_access off, technicians only get the
-- RustDesk "Remote desktop (Prompt)" session, which the person at the device
-- accepts; file transfer and terminal stay available. Existing companies keep
-- unattended access.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS rustdesk_unattended_access TINYINT(1) NOT NULL DEFAULT 1;
