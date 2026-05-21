# Email Template Variables for Ticket Automations

This document describes the template variables available for use in email notifications triggered by ticket events.

## Common Template Variables

When configuring email notifications in automation actions, you can use the following template variables:

### Ticket Information
- `{{ticket.id}}` - Ticket ID
- `{{ticket.number}}` or `{{ticket.ticket_number}}` - Ticket number (e.g., "TKT-123")
- `{{ticket.subject}}` - Ticket subject/title
- `{{ticket.description}}` - Ticket description
- `{{ticket.priority}}` - Ticket priority (e.g., "high", "normal", "low")
- `{{ticket.status}}` - Ticket status (e.g., "open", "closed")
- `{{ticket.category}}` - Ticket category

### Requester Information
- `{{ticket.requester.email}}` - Requester email address
- `{{ticket.requester.display_name}}` - Requester full name
- `{{ticket.requester.first_name}}` - Requester first name
- `{{ticket.requester.last_name}}` - Requester last name

### Company Information
- `{{ticket.company.name}}` - Company name
- `{{ticket.company.id}}` - Company ID

### Assigned User
- `{{ticket.assigned_user.email}}` - Assigned user email
- `{{ticket.assigned_user.display_name}}` - Assigned user name

### Watchers
- `{{ticket.watcher_emails}}` - Array of watcher email addresses
- `{{ticket.watchers_count}}` - Number of watchers

### Timestamps
- `{{ticket.created_at}}` - When the ticket was created (ISO format)
- `{{ticket.updated_at}}` - When the ticket was last updated (ISO format)

## Example Usage

### Simple Email Notification
```json
{
  "recipients": ["support@example.com"],
  "subject": "New Ticket: {{ticket.subject}}",
  "html": "<h2>Ticket {{ticket.number}}</h2><p><strong>From:</strong> {{ticket.requester.display_name}} ({{ticket.requester.email}})</p><p><strong>Company:</strong> {{ticket.company.name}}</p><p><strong>Description:</strong></p><p>{{ticket.description}}</p>",
  "text": "Ticket {{ticket.number}}: {{ticket.subject}}\nFrom: {{ticket.requester.display_name}}\nDescription: {{ticket.description}}"
}
```

### Notification with Priority
```json
{
  "recipients": ["admin@example.com"],
  "subject": "[{{ticket.priority}}] {{ticket.subject}}",
  "html": "<p>Priority {{ticket.priority}} ticket created by {{ticket.requester.display_name}}</p>"
}
```

## Important Notes

1. **Empty Values**: If a template variable doesn't have a value (e.g., no assigned user), it will be replaced with an empty string.

2. **Missing Keys**: If a template key is not present in the payload at all, the email module will use default values:
   - Default subject: "Automation notification"
   - Default body: "Automation triggered."

3. **Template Rendering**: Template variables are rendered by the automation service before being sent to the email module, so by the time the email is sent, all variables should be replaced with actual values.

4. **Uppercase Tokens**: You can also use uppercase token names like `{{TICKET_SUBJECT}}`, `{{TICKET_NUMBER}}`, etc., which are generated from the ticket context.

## Troubleshooting

If your template variables are not rendering:

1. **Check the automation context**: Ensure the ticket context is being passed to the automation
2. **Verify variable names**: Template variables are case-sensitive
3. **Check for typos**: Common mistakes include `{{email.ticket.subject}}` (incorrect) vs `{{ticket.subject}}` (correct)
4. **Test with simple variables first**: Start with `{{ticket.number}}` to verify the basic context is working

## Migration from Old Variables

If you were previously using variables like:
- `{{email.new.ticket.body}}` → Use `{{ticket.description}}`
- `{{email.ticket.subject}}` → Use `{{ticket.subject}}`
- `{{TICKET_SUMMARY}}` → Use `{{ticket.subject}}` or define a custom message template

These old variables were never part of the system and should be replaced with the documented ticket context variables above.
