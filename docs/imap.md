# IMAP Module

The IMAP module ingests support emails into tickets. Each mailbox is configured with its own schedule and folder selection.

## Web interface

Administrators can manage mailboxes at `/admin/modules/imap`. The workspace allows:

- Creating mailboxes with encrypted credentials
- Selecting a source folder and optional company association
- Setting a mailbox priority to control processing order
- Choosing to only process unread messages and whether to mark processed mail as read
- Configuring a cron schedule per mailbox
- Triggering manual synchronisation or deleting existing mailboxes
- Cloning existing configurations to rapidly provision new mailboxes

## API endpoints

All IMAP endpoints require super administrator access.

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/imap/accounts` | List all IMAP mailboxes. |
| `POST` | `/api/imap/accounts` | Create a new mailbox. |
| `GET` | `/api/imap/accounts/{accountId}` | Retrieve mailbox details. |
| `PUT` | `/api/imap/accounts/{accountId}` | Update mailbox configuration. |
| `DELETE` | `/api/imap/accounts/{accountId}` | Remove a mailbox and its processing schedule. |
| `POST` | `/api/imap/accounts/{accountId}/sync` | Run an immediate synchronisation for a mailbox. |
| `POST` | `/api/imap/accounts/{accountId}/clone` | Duplicate an existing mailbox configuration. |

Responses expose scheduled task identifiers and the last synchronisation timestamp so external tooling can audit ingestion.

## Scheduling

Each mailbox stores its cron expression and a priority. When a mailbox is created or updated the platform provisions a scheduled task using the `imap_sync:{id}` command so that the background scheduler can trigger the importer at the requested cadence. Bulk synchronisation processes mailboxes in ascending priority order (so 0 runs before 10) so critical inboxes ingest mail first.

Manual synchronisation is available through the API or the workspace actions.

When a deployment marks a pending restart (for example while `scripts/upgrade.sh`
is running) the importer temporarily pauses new IMAP fetches. The scheduler
retries automatically once the restart flag clears so imports resume after the
update completes.

## Ticket association

During import the sender address is analysed to automatically associate the ticket with a company. The importer compares the sender's email domain with the configured company email domains and, when a match is found, links the ticket to that company. If the sender already exists as a staff contact for the matched company the ticket requester is also set accordingly. When no domain match exists the importer falls back to the company assigned to the mailbox configuration and still attempts to link the requester by email address.

## Security

Mailbox passwords are encrypted at rest using the platform secret key. Synchronisation respects the "unread only" and "mark as read" toggles to prevent duplicate imports, and the importer stores processed message UIDs to avoid reprocessing previously ingested mail.
