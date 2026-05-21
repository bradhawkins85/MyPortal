# Create Ticket Automation Module

## Overview

The `create-ticket` module enables automated ticket creation through scheduled company automations. This module supports full variable interpolation, allowing dynamic ticket content based on system context and scheduled triggers.

## Features

- **Automatic ticket creation** from scheduled automations
- **Full variable interpolation** for dynamic content
- **Flexible configuration** with support for all ticket fields
- **Webhook tracking** for monitoring automation execution
- **Error handling** with detailed logging

## Configuration

### Module Settings

The create-ticket module requires no special configuration. Simply enable it in the integration modules section.

### Required Fields

- `subject` (string): The ticket subject line

### Optional Fields

- `description` (string): Detailed description of the ticket
- `company_id` (integer): ID of the company associated with the ticket
- `requester_id` (integer): ID of the user requesting the ticket
- `assigned_user_id` (integer): ID of the user to assign the ticket to
- `priority` (string): Ticket priority (default: "normal")
- `status` (string): Ticket status (default: "open")
- `category` (string): Ticket category
- `module_slug` (string): Integration module identifier
- `external_reference` (string): External system reference ID

## Usage Examples

### Example 1: Simple Scheduled Ticket

Create a weekly maintenance ticket:

```json
{
  "name": "Weekly Maintenance Reminder",
  "kind": "scheduled",
  "cadence": "weekly",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Weekly System Maintenance",
          "description": "Perform scheduled weekly maintenance tasks",
          "priority": "normal",
          "status": "pending"
        }
      }
    ]
  }
}
```

### Example 2: Ticket with Variable Interpolation

Create a company-specific ticket using variables:

```json
{
  "name": "Monthly Review for Companies",
  "kind": "scheduled",
  "cron_expression": "0 9 1 * *",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Monthly review for {{company.name}}",
          "description": "Scheduled monthly review at {{timestamp}}\n\nCompany: {{company.name}}\nCompany ID: {{company.id}}",
          "company_id": "{{company.id}}",
          "priority": "high",
          "status": "open",
          "category": "review"
        }
      }
    ]
  }
}
```

### Example 3: Multi-Action Automation

Combine ticket creation with notifications:

```json
{
  "name": "Critical System Alert",
  "kind": "scheduled",
  "cadence": "daily",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Daily System Health Check",
          "description": "Automated system health check for {{company.name}}",
          "company_id": "{{company.id}}",
          "priority": "high",
          "category": "monitoring"
        }
      },
      {
        "module": "smtp",
        "payload": {
          "subject": "Ticket Created: Daily Health Check",
          "recipients": ["admin@example.com"],
          "html": "<p>A new health check ticket has been created for {{company.name}}</p>"
        }
      }
    ]
  }
}
```

## Available Variables

The automation system provides various variables that can be used in the payload:

### System Variables
- `{{timestamp}}` - Current date/time in ISO format
- `{{system.name}}` - System name
- `{{system.version}}` - System version

### Company Variables (when available)
- `{{company.id}}` - Company ID
- `{{company.name}}` - Company name

### User Variables (when available)
- `{{user.id}}` - User ID
- `{{user.email}}` - User email
- `{{user.display_name}}` - User display name

### Ticket Variables (in event-based automations)
- `{{ticket.id}}` - Ticket ID
- `{{ticket.number}}` - Ticket number
- `{{ticket.subject}}` - Ticket subject
- `{{ticket.status}}` - Ticket status
- `{{ticket.priority}}` - Ticket priority

## Best Practices

1. **Use Descriptive Subjects**: Make ticket subjects clear and searchable
2. **Include Context**: Add relevant details in the description
3. **Set Appropriate Priorities**: Use priority levels to manage workflow
4. **Prevent Recursion**: The module automatically sets `trigger_automations=False`
5. **Monitor Execution**: Check webhook events to track automation success

## Troubleshooting

### Common Issues

**Issue: Tickets not being created**
- Verify the create-ticket module is enabled
- Check that the automation status is "active"
- Review webhook events for error messages

**Issue: Variables not interpolated**
- Ensure variable syntax is correct: `{{variable.path}}`
- Verify the variable exists in the automation context
- Check for typos in variable names

**Issue: Invalid field values**
- Ensure company_id, requester_id, and assigned_user_id are valid integers
- Verify status and priority values match system configuration
- Check that required fields (subject) are provided

## API Integration

The create-ticket module can also be triggered programmatically:

```python
from app.services import modules

# Trigger the module
result = await modules.trigger_module(
    "create-ticket",
    {
        "subject": "API-created ticket",
        "description": "Created via API",
        "priority": "high",
    },
    background=False
)

# Check result
if result["status"] == "succeeded":
    ticket_id = result["ticket_id"]
    ticket_number = result["ticket_number"]
```

## Testing

To test the create-ticket module in your automation:

1. Create a test automation with a short interval (e.g., every 5 minutes)
2. Set status to "active"
3. Monitor the automation_runs table for execution
4. Check the tickets table for created tickets
5. Review webhook_events for detailed execution logs

## Security Considerations

- The module respects user permissions and company associations
- Tickets are created without triggering additional automations to prevent loops
- All ticket creation is logged via webhook events
- Sensitive data in variables is not logged in webhook payloads
