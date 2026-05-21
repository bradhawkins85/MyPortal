# Scheduled Ticket Creation

## Overview

The `create_scheduled_ticket` command enables automated ticket creation through the scheduled tasks system. This command allows you to create tickets on a recurring schedule using cron expressions.

## Features

- **Scheduled ticket creation** via cron schedules
- **JSON payload configuration** for flexible ticket details
- **Company-specific or global** scheduled tasks
- **Full ticket field support** including priority, status, category, and assignments
- **Error handling and logging** with detailed execution tracking

## Usage

### Creating a Scheduled Ticket Task

1. Navigate to **System Automation** or the company's **Automations** section in the company edit page
2. Click **New task** or **New automation**
3. Select **Create scheduled ticket** from the Command dropdown
4. Configure the schedule using a cron expression (e.g., `0 9 * * 1` for every Monday at 9 AM UTC)
5. Enter the JSON payload with ticket details

### JSON Payload Format

The JSON payload should be entered in the **JSON Payload** field that appears when you select the `create_scheduled_ticket` command.

Required fields:
- `subject` (string): The ticket subject line

Optional fields:
- `description` (string): Detailed description of the ticket
- `requester_id` (integer): ID of the user requesting the ticket
- `assigned_user_id` (integer): ID of the user to assign the ticket to
- `priority` (string): Ticket priority (default: "normal")
- `status` (string): Ticket status (default: "open")
- `category` (string): Ticket category
- `module_slug` (string): Integration module identifier
- `external_reference` (string): External system reference ID

### Example JSON Payloads

#### Simple Weekly Maintenance Ticket

```json
{
  "subject": "Weekly System Maintenance",
  "description": "Perform weekly system maintenance tasks",
  "priority": "normal",
  "status": "pending"
}
```

#### Monthly Review Ticket with Assignment

```json
{
  "subject": "Monthly Security Review",
  "description": "Review security logs and update policies",
  "priority": "high",
  "status": "open",
  "category": "security",
  "assigned_user_id": 5
}
```

#### Automated Backup Verification

```json
{
  "subject": "Daily Backup Verification",
  "description": "Verify all backup jobs completed successfully",
  "priority": "high",
  "status": "pending",
  "category": "maintenance"
}
```

## Cron Expression Examples

- `0 9 * * 1`: Every Monday at 9:00 AM UTC
- `0 2 * * *`: Every day at 2:00 AM UTC
- `0 9 1 * *`: First day of every month at 9:00 AM UTC
- `0 17 * * 5`: Every Friday at 5:00 PM UTC
- `0 0 1 1 *`: January 1st at midnight UTC

## Task Execution Details

When a scheduled ticket creation task runs:

1. The task reads the JSON payload from the description field
2. Validates the JSON syntax and required fields
3. Creates the ticket using the provided parameters
4. Uses the task's company_id if set (company-specific tasks)
5. Logs the execution result with ticket ID and number
6. Records success or failure in the task run history

## Troubleshooting

### Common Issues

**Issue: "Invalid JSON payload" error**
- Verify your JSON syntax is correct
- Use a JSON validator to check for syntax errors
- Ensure all strings are properly quoted
- Check for trailing commas

**Issue: "Missing required field: subject" error**
- Ensure the `subject` field is present in your JSON payload
- Verify the field name is spelled correctly

**Issue: Ticket created with wrong company**
- For company-specific tasks, verify the automation is associated with the correct company
- Check the company_id in the task configuration

**Issue: Tickets not being created on schedule**
- Verify the task is active (checkbox enabled)
- Check the cron expression is valid
- Review the task run history for error messages
- Ensure the scheduled task service is running

## Automation Prevention

To prevent infinite loops, scheduled ticket creation automatically sets `trigger_automations=False`. This means tickets created via this command will NOT trigger event-based automations (e.g., ticket-created events).

If you need to trigger automations when creating tickets on a schedule, consider using the automation system's scheduled automations with the `create-ticket` module instead.

## Best Practices

1. **Use descriptive subjects**: Make ticket subjects clear and searchable
2. **Set appropriate priorities**: Use priority levels to manage workflow effectively
3. **Schedule during off-hours**: Consider scheduling during low-activity periods
4. **Monitor execution**: Regularly check task run history for failures
5. **Test with manual runs**: Use "Run now" to test configurations before relying on schedules
6. **Document your schedules**: Use the description field to note what the task does and when it runs

## API Integration

Scheduled tickets can be managed via the Scheduler API:

```python
# Create a scheduled ticket task
POST /scheduler/tasks
{
  "name": "Weekly maintenance ticket",
  "command": "create_scheduled_ticket",
  "cron": "0 9 * * 1",
  "description": "{\"subject\": \"Weekly Maintenance\", \"priority\": \"normal\"}",
  "company_id": 10,
  "active": true
}
```

## Security Considerations

- Scheduled ticket creation respects user permissions and company associations
- All ticket creation is logged via the task run history
- JSON payloads are validated before execution
- Invalid or malformed JSON will cause the task to fail safely
