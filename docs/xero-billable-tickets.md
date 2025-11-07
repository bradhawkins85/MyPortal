# Xero Billable Tickets Integration

This feature enables automatic synchronization of billable ticket time entries to Xero, creating invoices for completed work.

## Overview

When the "Sync to Xero" scheduler runs, the system will:

1. Find tickets matching configured "billable statuses"
2. Group billable time entries by ticket and labour type
3. Create invoice line items in Xero
4. Track billed time entries to prevent duplicate billing
5. Move billed tickets to "Closed" status
6. Record the Xero invoice number on each ticket

## Configuration

### Xero Module Settings

Configure the following settings in **Admin > Modules > Xero**:

- **Billable Statuses**: Comma-separated list of ticket statuses that should be billed (e.g., "resolved, completed")
- **Default Hourly Rate**: The hourly rate to charge for billable time
- **Account Code**: Xero account code for ticket billing (default: "400")
- **Tax Type**: Xero tax type to apply
- **Line Amount Type**: "Exclusive" or "Inclusive"
- **Reference Prefix**: Prefix for invoice reference (default: "Support")
- **Line Item Description Template**: Template for invoice line item descriptions

### Line Item Description Template Variables

Available template variables:
- `{ticket_id}` - The ticket ID number
- `{ticket_subject}` - The ticket subject/title
- `{ticket_status}` - The ticket status
- `{labour_name}` - Name of the labour type
- `{labour_code}` - Code of the labour type
- `{labour_minutes}` - Minutes spent on this labour type
- `{labour_hours}` - Hours spent (decimal format)
- `{labour_suffix}` - " · Labour Type Name" if present, empty otherwise

Default template: `Ticket {ticket_id}: {ticket_subject}{labour_suffix}`

Example custom template: `#{ticket_id} - {ticket_subject} - {labour_name} ({labour_hours}h)`

## How It Works

### 1. Time Entry

Technicians add time entries to tickets with:
- Minutes spent
- Billable flag (checked for billable time)
- Labour type (optional, e.g., "Remote Support", "On-site")

### 2. Ticket Status

When a ticket reaches a billable status (e.g., "resolved"), it becomes eligible for billing.

### 3. Sync to Xero

The scheduled task "Sync to Xero" runs for each company:
1. Processes recurring invoice items (existing functionality)
2. Processes billable tickets (new functionality)

For billable tickets:
- Groups all unbilled time entries by ticket and labour type
- Creates one invoice line item per ticket per labour type
- Submits invoice to Xero as DRAFT
- Records billed time entries in database
- Updates ticket with invoice number and "billed_at" timestamp
- Closes the ticket

### 4. Prevention of Duplicate Billing

Once time entries are billed:
- They are recorded in the `ticket_billed_time_entries` table
- Future sync runs will skip these entries
- Technicians cannot add new time to billed tickets
- Attempts to add time to billed tickets show an error message

### 5. Invoice Display

On the ticket detail page, billed tickets show:
- Invoice number
- Date billed
- Message indicating the ticket is closed and billed

## Database Schema

### New Fields on `tickets` Table

```sql
xero_invoice_number VARCHAR(64) NULL    -- Invoice number from Xero
billed_at DATETIME(6) NULL              -- When the ticket was billed
```

### New Table: `ticket_billed_time_entries`

Tracks which time entries have been billed to prevent duplicates:

```sql
CREATE TABLE ticket_billed_time_entries (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    reply_id INT NOT NULL,
    xero_invoice_number VARCHAR(64) NOT NULL,
    billed_at DATETIME(6) NOT NULL,
    minutes_billed INT NOT NULL,
    labour_type_id INT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY (reply_id),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id),
    FOREIGN KEY (reply_id) REFERENCES ticket_replies(id),
    FOREIGN KEY (labour_type_id) REFERENCES ticket_labour_types(id)
);
```

## API Endpoints

No new public API endpoints. The sync is triggered via scheduled tasks.

### Scheduled Task Command

```json
{
  "command": "sync_to_xero",
  "company_id": 123
}
```

## Validation Rules

### Time Entry Restrictions

- ✅ Can add time to open, in progress, pending tickets
- ✅ Can add time to resolved/completed tickets (if not yet billed)
- ❌ Cannot add time to billed tickets (has `xero_invoice_number`)
- ❌ Cannot update time entries on billed tickets

### Billing Requirements

For a ticket to be billed:
1. Status must match one of the "billable statuses"
2. Must have at least one unbilled, billable time entry
3. Must belong to a company
4. Company must have Xero configuration

## Testing

Run tests with:

```bash
pytest tests/test_xero_billable_tickets.py -v
```

Test coverage includes:
- Status filtering
- Unbilled time detection
- Labour type grouping
- Template rendering
- Duplicate billing prevention

## Troubleshooting

### Tickets Not Being Billed

Check:
1. Ticket status matches configured "billable statuses"
2. Ticket has billable time entries (checkbox is checked)
3. Time entries are not already billed
4. Xero module is enabled and configured
5. Company has Xero ID or name configured
6. Hourly rate is set and greater than zero

### View Webhook Monitor

Check **Admin > Webhook Monitor** for Xero API calls:
- Event name: `xero.sync.billable_tickets`
- View request/response details
- Check for error messages

### Database Queries

Check if time entries are marked as billed:

```sql
SELECT * FROM ticket_billed_time_entries 
WHERE ticket_id = ?;
```

Check ticket billing status:

```sql
SELECT id, subject, status, xero_invoice_number, billed_at 
FROM tickets 
WHERE xero_invoice_number IS NOT NULL;
```

## Migration

The migration `109_ticket_xero_billing.sql` adds:
- `xero_invoice_number` and `billed_at` columns to `tickets`
- `ticket_billed_time_entries` table
- Appropriate indexes and foreign keys

Migration runs automatically on application startup.

## Example Workflow

1. **Create Ticket**: Customer reports email issue
2. **Work on Ticket**: Technician adds time entries
   - 30 minutes remote support (billable)
   - 15 minutes on-site visit (billable)
3. **Resolve Ticket**: Technician marks ticket as "Resolved"
4. **Sync Runs**: Scheduled task processes company
5. **Invoice Created**: Xero receives invoice with 2 line items:
   - Remote Support: 0.5 hours × $150 = $75
   - On-site Support: 0.25 hours × $150 = $37.50
6. **Ticket Closed**: Ticket moves to "Closed" status
7. **Invoice Number**: Ticket shows "INV-001234"
8. **Time Locked**: No further time can be added

## Future Enhancements

Potential improvements:
- Manual "Bill Now" button for individual tickets
- Preview invoice before sending to Xero
- Support for discounts or adjustments
- Batch billing summary reports
- Integration with Xero's "Approved" status
- Automatic email notification to customers

## Support

For issues or questions:
1. Check the Webhook Monitor for API errors
2. Review scheduled task logs
3. Verify Xero module configuration
4. Check database records for billing status
