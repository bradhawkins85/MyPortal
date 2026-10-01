-- TRMM agents can report several network adapters. Retain every address so
-- network discovery can match an asset through any of its interfaces.
ALTER TABLE assets MODIFY COLUMN mac_address TEXT NULL;
