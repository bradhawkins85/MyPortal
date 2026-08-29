# DMARC aggregate reporting operations

## DNS and mailbox setup

Enable the feature by creating or editing an account on **Microsoft 365 Mail** and
setting its import purpose to **DMARC aggregate reports**. Its address must be
`DMARC@your-reporting-domain`; only one mailbox may have this purpose. Copy each company's exact value, for example
`rua=mailto:DMARC+<random-code>@reports.example.com`, into its `_dmarc` TXT
record. The random code is a routing capability and must not be replaced with a
database ID.

For Microsoft 365, license the shared mailbox where required, enable authenticated
IMAP (or use the Graph-backed account), and restrict access to the ingestion
identity. Microsoft Graph `Mail.ReadWrite` access is required. Exchange Online plus addressing must be enabled and a transport test
must confirm arbitrary `DMARC+tag` recipients retain the envelope recipient. If
the tenant disables plus addressing, create one alias per company and a transport
rule mapping each alias to the corresponding tagged recipient before enabling
collection.

Polling is registered through MyPortal's scheduled Microsoft Graph mailbox infrastructure. It
uses the provider UID/message ID, persists every attachment before moving or
marking the message, retries transient failures with backoff, and leaves failed
imports available for reprocessing.

## Formats, limits, and retention

XML, gzip containing one XML document, and zip containing flat XML members are
supported. Nested archives, traversal paths, malformed XML, and inputs exceeding
the configured compressed, expanded, attachment, XML-depth, or record-count
limits are quarantined. Keep quarantine long enough to diagnose sender problems.
Processed data is retained for `DMARC_RETENTION_DAYS` (365 by default); operators
must align mailbox and database deletion jobs with their contractual obligations.

DMARC source IPs may identify organisations or individuals and should be treated
as personal/security data. Limit access with `dmarc.view`, record exports in the
audit log, encrypt backups, and document the lawful purpose and retention period.

## Troubleshooting

1. Confirm DNS uses the exact company `rua` address and reporting domain.
2. Send a tagged delivery and verify Exchange preserves the envelope recipient.
3. Check scheduler health, IMAP authentication, message UID, and retry state.
4. Inspect quarantine (not application logs) for safe failure reasons.
5. Increase a limit only after checking the archive and expected sender volume.
6. Rotate a leaked company code with explicit confirmation, then update DNS; old
   codes become unassigned and subsequent deliveries are quarantined.
