-- Microsoft Graph message IDs are opaque, case-sensitive identifiers.  The
-- database default is commonly case-insensitive, which made IDs that differed
-- only by letter case share one import marker and could associate a new email
-- with an unrelated ticket.
ALTER TABLE m365_mail_account_messages
    MODIFY message_uid VARCHAR(512)
    CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL;
