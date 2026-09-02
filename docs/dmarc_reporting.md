# DMARC aggregate and forensic reporting operations

## DNS and mailbox setup

Enable the feature by creating or editing an account on **Microsoft 365 Mail** and
setting its import purpose to **DMARC aggregate and forensic reports**. DMARC mailbox
accounts are global and do not use a selected company. MyPortal reads each aggregate
report's `policy_published/domain` value and matches it against configured
`company_email_domains`, then stores the report under the matching company. Put the
applicable mailbox address directly in each domain's `_dmarc` TXT record, for example
`rua=mailto:dmarc@reports.customer.example` and
`ruf=mailto:dmarc@reports.customer.example`. This allows one DMARC mailbox to safely
ingest reports for multiple companies without tagged addresses or per-company mailbox
imports.

For Microsoft 365, license the shared mailbox where required, enable authenticated
IMAP (or use the Graph-backed account), and restrict access to the ingestion
identity. Microsoft Graph `Mail.ReadWrite` access is required.

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

DMARC source IPs and the sender/recipient addresses present in RUF reports may
identify organisations or individuals and should be treated
as personal/security data. Limit access with `dmarc.view`, record exports in the
audit log, encrypt backups, and document the lawful purpose and retention period.

## Troubleshooting

1. Confirm each domain's DNS uses one of the configured DMARC mailbox addresses for its `rua` and `ruf` value.
2. Confirm the mailbox import purpose is DMARC and each reported policy domain exists in a company's email domains.
3. Check scheduler health, IMAP authentication, message UID, and retry state.
4. Inspect quarantine (not application logs) for safe failure reasons.
5. Increase a limit only after checking the archive and expected sender volume.
