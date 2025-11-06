# Create-Ticket Automation Examples

## Real-World Use Cases

### 1. Scheduled System Maintenance

**Scenario:** Automatically create a maintenance ticket every Sunday at 2 AM.

```json
{
  "name": "Weekly System Maintenance",
  "description": "Create a weekly maintenance reminder ticket",
  "kind": "scheduled",
  "cron_expression": "0 2 * * 0",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Weekly System Maintenance - Week of {{timestamp}}",
          "description": "## Scheduled Maintenance Tasks\n\n- [ ] Database optimization\n- [ ] Log rotation\n- [ ] Security updates\n- [ ] Backup verification\n\nScheduled: {{timestamp}}",
          "priority": "normal",
          "status": "pending",
          "category": "maintenance"
        }
      }
    ]
  }
}
```

**Result:** Creates a ticket every Sunday with a checklist of maintenance tasks.

---

### 2. Company-Specific Monthly Reviews

**Scenario:** Create monthly review tickets for each company on the 1st of every month.

**Note:** When the automation runs, the context will include company information if the automation is scoped to companies.

```json
{
  "name": "Monthly Company Review",
  "description": "Create monthly review tickets for companies",
  "kind": "scheduled",
  "cron_expression": "0 9 1 * *",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Monthly Review - {{company.name}}",
          "description": "## Monthly Review for {{company.name}}\n\nCompany ID: {{company.id}}\nReview Date: {{timestamp}}\n\n### Items to Review:\n- Service usage\n- Outstanding tickets\n- Account status\n- Upcoming renewals",
          "company_id": "{{company.id}}",
          "priority": "high",
          "status": "open",
          "category": "review"
        }
      },
      {
        "module": "smtp",
        "payload": {
          "subject": "Monthly Review Ticket Created for {{company.name}}",
          "recipients": ["manager@example.com"],
          "html": "<p>A monthly review ticket has been created for <strong>{{company.name}}</strong></p><p>Please review at your earliest convenience.</p>"
        }
      }
    ]
  }
}
```

**Result:** Creates a review ticket for each company and sends an email notification.

---

### 3. License Expiration Reminders

**Scenario:** Create reminder tickets for software licenses expiring soon.

```json
{
  "name": "License Expiration Reminder",
  "description": "Remind about expiring licenses 30 days in advance",
  "kind": "scheduled",
  "cadence": "daily",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "License Expiration Alert - Review Required",
          "description": "## License Review Required\n\nPlease review all software licenses expiring in the next 30 days.\n\nGenerated: {{timestamp}}\n\n### Action Items:\n- [ ] Review Microsoft 365 licenses\n- [ ] Review antivirus subscriptions\n- [ ] Review SSL certificates\n- [ ] Update license inventory",
          "priority": "high",
          "status": "open",
          "category": "compliance",
          "assigned_user_id": "1"
        }
      }
    ]
  }
}
```

**Result:** Creates a daily reminder ticket assigned to the admin to review expiring licenses.

---

### 4. End-of-Month Reporting

**Scenario:** Automatically create an end-of-month reporting task.

```json
{
  "name": "End of Month Reporting",
  "description": "Create monthly reporting ticket on the last day of each month",
  "kind": "scheduled",
  "cron_expression": "0 16 28-31 * *",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "End of Month Report - {{timestamp}}",
          "description": "## Monthly Reporting Tasks\n\nGenerate and submit monthly reports:\n\n- [ ] Ticket statistics\n- [ ] SLA compliance report\n- [ ] Customer satisfaction metrics\n- [ ] Time tracking summary\n- [ ] Revenue report\n\nDeadline: End of business today",
          "priority": "high",
          "status": "open",
          "category": "reporting",
          "assigned_user_id": "2"
        }
      },
      {
        "module": "ntfy",
        "payload": {
          "message": "End of month reporting ticket created",
          "priority": "high",
          "title": "Monthly Reporting Reminder"
        }
      }
    ]
  }
}
```

**Result:** Creates a reporting ticket and sends a push notification on the last days of each month.

---

### 5. Event-Based Ticket Creation

**Scenario:** Create a follow-up ticket when a ticket is closed.

```json
{
  "name": "Customer Satisfaction Follow-up",
  "description": "Create satisfaction survey ticket when tickets are closed",
  "kind": "event",
  "trigger_event": "tickets.closed",
  "trigger_filters": {
    "match": {
      "ticket.priority": ["high", "critical"]
    }
  },
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Customer Satisfaction Survey - Ticket {{ticket.number}}",
          "description": "## Follow-up Survey\n\nOriginal Ticket: {{ticket.number}}\nSubject: {{ticket.subject}}\nClosed: {{timestamp}}\n\nPlease contact the customer to ensure their issue was fully resolved and collect satisfaction feedback.",
          "company_id": "{{ticket.company.id}}",
          "requester_id": "{{ticket.requester.id}}",
          "priority": "normal",
          "status": "open",
          "category": "follow-up",
          "external_reference": "FOLLOW-{{ticket.number}}"
        }
      }
    ]
  }
}
```

**Result:** When a high or critical priority ticket is closed, a follow-up survey ticket is automatically created.

---

### Subject Wildcard Matching for Ticket Events

**Scenario:** Trigger an automation when ticket subjects match familiar patterns.

Use SQL-style wildcards (`%` for any length, `_` for a single character) inside the `trigger_filters` block. Escape literal percent or underscore characters with a backslash (`\%`, `\_`).

```json
{
  "name": "Subject Pattern Routing",
  "description": "Route known subject patterns to the correct queue",
  "kind": "event",
  "trigger_event": "tickets.created",
  "trigger_filters": {
    "any": [
      {
        "match": {
          "ticket.subject": "My computer%"
        }
      },
      {
        "match": {
          "ticket.subject": "% wont turn on"
        }
      },
      {
        "match": {
          "ticket.subject": "New User % Onboarding"
        }
      }
    ]
  },
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Escalated ticket: {{ticket.subject}}",
          "description": "Wildcard routing matched {{ticket.subject}} at {{timestamp}}.",
          "queue": "Escalations"
        }
      }
    ]
  }
}
```

**Result:** Tickets with subjects like “My computer is frozen”, “Server wont turn on”, or “New User HR Onboarding” automatically route into the escalations queue.

---

### 6. Multi-Company Onboarding

**Scenario:** Create an onboarding ticket for new companies.

```json
{
  "name": "New Company Onboarding",
  "description": "Create onboarding checklist for new companies",
  "kind": "event",
  "trigger_event": "company.created",
  "status": "active",
  "action_payload": {
    "actions": [
      {
        "module": "create-ticket",
        "payload": {
          "subject": "Onboarding Checklist - {{company.name}}",
          "description": "## Welcome {{company.name}}!\n\nOnboarding started: {{timestamp}}\n\n### Setup Tasks:\n- [ ] Configure company settings\n- [ ] Add company logo\n- [ ] Set up user accounts\n- [ ] Configure integrations\n- [ ] Schedule kickoff meeting\n- [ ] Send welcome email\n- [ ] Assign account manager\n\n### Timeline:\n- Day 1-3: Account setup\n- Day 4-7: Training sessions\n- Week 2: Go-live support",
          "company_id": "{{company.id}}",
          "priority": "high",
          "status": "open",
          "category": "onboarding",
          "module_slug": "automation"
        }
      },
      {
        "module": "smtp",
        "payload": {
          "subject": "New Company Onboarding - {{company.name}}",
          "recipients": ["sales@example.com", "support@example.com"],
          "html": "<h2>New Company Onboarding</h2><p>Company: <strong>{{company.name}}</strong></p><p>ID: {{company.id}}</p><p>An onboarding ticket has been created. Please begin the onboarding process.</p>"
        }
      }
    ]
  }
}
```

**Result:** Creates an onboarding ticket and notifies the team when a new company is added.

---

## Variable Reference

### Available Variables

All variables are interpolated before the create-ticket module receives the payload:

| Variable | Description | Example |
|----------|-------------|---------|
| `{{timestamp}}` | Current timestamp | `2025-01-15T14:30:00Z` |
| `{{company.id}}` | Company ID | `42` |
| `{{company.name}}` | Company name | `Acme Corp` |
| `{{ticket.id}}` | Ticket ID (in event automations) | `123` |
| `{{ticket.number}}` | Ticket number (in event automations) | `TKT-123` |
| `{{ticket.subject}}` | Ticket subject (in event automations) | `Email not working` |
| `{{ticket.priority}}` | Ticket priority (in event automations) | `high` |
| `{{ticket.status}}` | Ticket status (in event automations) | `open` |
| `{{ticket.company.id}}` | Company ID from ticket | `42` |
| `{{ticket.company.name}}` | Company name from ticket | `Acme Corp` |
| `{{ticket.requester.id}}` | Requester user ID | `10` |
| `{{ticket.requester.email}}` | Requester email | `user@example.com` |
| `{{user.id}}` | User ID (when available) | `15` |
| `{{user.email}}` | User email (when available) | `admin@example.com` |
| `{{user.display_name}}` | User display name (when available) | `John Doe` |

### Testing Variables

To test variable interpolation before deploying:

1. Create a test automation with a short interval
2. Use simple variables first (e.g., `{{timestamp}}`)
3. Check the created ticket to verify interpolation
4. Add more complex variables gradually
5. Monitor webhook events for any errors

---

## Best Practices

1. **Start Simple:** Begin with static content, then add variables
2. **Test First:** Use short intervals for testing, then adjust to production schedules
3. **Monitor Logs:** Check webhook events to track automation execution
4. **Use Descriptive Names:** Make automation names clear and searchable
5. **Document Purpose:** Add descriptions explaining why each automation exists
6. **Set Appropriate Priorities:** Use priority levels to manage workflow effectively
7. **Include Context:** Add relevant details in descriptions for better clarity
8. **Combine Actions:** Use multiple actions for comprehensive workflows
9. **Handle Errors:** Review failed webhook events and adjust configurations
10. **Avoid Loops:** The system prevents recursion automatically, but be mindful of event chains
