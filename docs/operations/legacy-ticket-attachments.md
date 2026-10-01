# Legacy ticket attachments

Older email (IMAP) ingestion saved ticket attachments to
`app/static/uploads/tickets/` under random generated names. Current code stores
them in `private_uploads/tickets/`, which is never exposed directly.

## What changed

- `/static/uploads/tickets/...` now always returns 404. Nothing in the portal
  links to that URL; attachments are downloaded only through the
  access-controlled ticket attachment endpoints.
- Those endpoints (and the WhisperX transcription module) look in
  `private_uploads/tickets/` first and fall back to the legacy folder, so
  attachments that have not been moved yet stay downloadable for users who are
  allowed to see the ticket.

## Moving the files

`ticket_attachments.filename` holds only the bare generated file name (no
directory), so moving a file needs no database update.

```
python manage.py migrate-legacy-ticket-attachments --dry-run
python manage.py migrate-legacy-ticket-attachments
```

The same step runs automatically at the end of `python manage.py migrate`
(failures there are printed and never block the schema phase).

The command is idempotent:

- files are copied to `private_uploads/tickets/` with mode `0600`, then the
  legacy copy is removed (this works across Docker volumes);
- a legacy file whose name already exists in private storage with identical
  content is removed from the legacy folder;
- a name clash with different content is left in place, logged, and the
  command exits with status 1 so it can be reviewed by hand;
- symlinks, dot files and sub-directories are skipped.
