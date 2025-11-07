# Xero Auto Send Invoices

## Overview

The **Sync to Xero (Auto Send)** scheduled task provides automated invoice synchronization with automatic approval and email delivery to customers. This task extends the standard **Sync to Xero** functionality by automatically setting invoices to "AUTHORISED" status (which displays as "Awaiting Payment" in the Xero UI) and instructing Xero to email them to the contact.

## Differences from Standard Sync to Xero

| Feature | Sync to Xero | Sync to Xero (Auto Send) |
|---------|--------------|---------------------------|
| Invoice Status | DRAFT | AUTHORISED |
| Displayed Status in Xero | Draft | Awaiting Payment |
| Email Sent | No | Yes (via Xero) |
| Use Case | Manual review before sending | Fully automated billing |

## Usage

### Creating an Auto-Send Task

1. Navigate to **System Automation** or the company's **Automations** section
2. Click **New task** or **New automation**
3. Select **Sync to Xero (Auto Send)** from the Command dropdown
4. Configure the schedule using a cron expression (e.g., `0 1 * * *` for daily at 1 AM UTC)
5. Select the company for which invoices should be synchronized
6. Save the task

### When to Use Auto Send

**Use Sync to Xero (Auto Send) when:**
- You have a well-established billing process with consistent time tracking
- Your team has confidence in the accuracy of billable time entries
- You want to streamline the invoicing workflow
- Customer invoices follow a regular, predictable schedule

**Use standard Sync to Xero when:**
- You want to review invoice details before sending
- Billing requires manual adjustments or approval
- You're testing or setting up the Xero integration
- You prefer to manually send invoices from within Xero

## How It Works

When the **Sync to Xero (Auto Send)** task runs:

1. **Collects billable items**: Gathers unbilled time entries from tickets and recurring invoice items
2. **Creates invoice payload**: Builds the invoice with line items, tax settings, and customer details
3. **Sets AUTHORISED status**: Unlike DRAFT invoices, AUTHORISED invoices are immediately ready for payment (displays as "Awaiting Payment" in Xero)
4. **Enables SentToContact flag**: Instructs Xero to send the invoice via email to the customer contact
5. **Records billing**: Marks time entries as billed and updates ticket statuses to "Closed"
6. **Logs the transaction**: Creates webhook monitor entries for tracking and debugging

## Invoice Status Lifecycle

```
Standard Sync:    [Tickets] → [DRAFT Invoice] → Manual Review → Manual Send → [AUTHORISED]
Auto Send:        [Tickets] → [AUTHORISED Invoice + Email] → [Customer Receives Invoice]
```

With auto-send, invoices skip the DRAFT stage and are immediately authorised and emailed, reducing manual intervention.

## Configuration Requirements

The auto-send task uses the same Xero module configuration as standard sync:

- **Xero OAuth credentials**: Client ID, client secret, refresh token, and tenant ID
- **Billable ticket statuses**: Comma-separated list of ticket statuses to invoice
- **Default hourly rate**: Rate for billing time entries
- **Account code**: Xero account code for invoice line items
- **Tax type**: Tax setting for invoices (e.g., "OUTPUT", "NONE")
- **Line amount type**: "Exclusive" or "Inclusive" tax handling
- **Reference prefix**: Text prefix for invoice references
- **Line item description template**: Template for generating line item text

These settings are configured once in **Admin → Integration Modules → Xero** and apply to both standard and auto-send tasks.

## Best Practices

1. **Start with standard sync**: Test your Xero integration with standard sync first to verify settings
2. **Monitor initial runs**: Check webhook monitor logs after enabling auto-send to ensure invoices are created correctly
3. **Schedule during off-hours**: Run auto-send tasks during periods when customers are unlikely to be working
4. **Set appropriate statuses**: Only include ticket statuses in "Billable ticket statuses" that indicate completed, reviewed work
5. **Review time entries regularly**: Ensure team members accurately track time before it's automatically billed
6. **Use company-specific tasks**: Create separate auto-send tasks for each company with unique billing schedules

## Monitoring and Troubleshooting

### Viewing Task Execution

1. Navigate to **System Automation** or company **Automations**
2. Find the auto-send task in the task list
3. Check the **Last Run** timestamp and **Status**
4. Click the task to view detailed execution history

### Checking Webhook Monitor

1. Navigate to **Admin → Webhook Monitor**
2. Filter by event name: `xero.sync.billable_tickets` or `xero.sync.company`
3. Review request/response details for debugging

### Common Issues

**Issue: Invoices created but not emailed**
- Verify the Xero contact has a valid email address
- Check that the contact's email settings in Xero allow invoice emails
- Review the webhook monitor response for Xero API errors

**Issue: Invoices remain in DRAFT status**
- Ensure you're using **Sync to Xero (Auto Send)** not standard **Sync to Xero**
- Check that the task command is set to `sync_to_xero_auto_send`
- Review error logs for API permission issues

**Issue: Duplicate invoices**
- Auto-send creates separate invoices per execution, same as standard sync
- Adjust the cron schedule to match your billing frequency
- Use "Run now" sparingly to avoid unintended duplicate invoices

## Cron Expression Examples

- `0 1 * * *`: Daily at 1:00 AM UTC
- `0 2 1 * *`: First day of each month at 2:00 AM UTC
- `0 3 * * 1`: Every Monday at 3:00 AM UTC
- `0 1 15,30 * *`: 15th and 30th of each month at 1:00 AM UTC

Remember that all scheduled tasks run in UTC time. Convert your desired local time to UTC when setting up the schedule.

## Security Considerations

- Auto-send invoices are immediately viewable by customers via Xero
- Ensure billable time entries are reviewed before the task runs
- Monitor the first few executions to verify accuracy
- Use webhook monitor logs to audit all invoice synchronization activity
- Invoices marked as billed cannot be re-billed, preventing duplicate charges

## API Integration

The auto-send functionality is accessible via the scheduler API by setting the command to `sync_to_xero_auto_send`:

```json
POST /scheduler/tasks
{
  "name": "Monthly auto-send invoicing",
  "command": "sync_to_xero_auto_send",
  "cron": "0 1 1 * *",
  "company_id": 10,
  "active": true
}
```

The underlying API call (`/services/xero/sync_company`) accepts an `auto_send` parameter set to `true` when invoked by this task.
