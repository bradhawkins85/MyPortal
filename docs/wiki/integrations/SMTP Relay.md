# SMTP Relay Module

The SMTP Relay module delivers automation notifications through the platform SMTP
server. Automations can target the relay either as a single action or as part of
an action group. The automation payload must supply a JSON object with the
following structure:

```json
{
  "module": "smtp",
  "payload": {
    "recipients": ["alerts@example.com"],
    "subject": "Ticket {{ ticket.number }} updated",
    "html": "<p>{{ ticket.summary }}</p>",
    "text": "Ticket {{ ticket.number }} has a new update."
  }
}
```

## Payload fields

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `recipients` | Array&lt;string&gt; or comma separated string | Optional | Overrides the module default recipients. When omitted the relay falls back to the `default_recipients` configured for the module. Blank entries are ignored. |
| `subject` | string | Optional | Sets the email subject. Defaults to `"Automation notification"`. The module prepends the configured `subject_prefix` before delivering the message. |
| `html` | string | Optional | HTML body for the message. If not provided the module checks the legacy `body` field before falling back to `<p>Automation triggered.</p>`. |
| `text` | string | Optional | Plain text alternative for the email body. When omitted the relay sends the HTML part only. |
| `context` | object | Optional | Populated automatically when the automation provides trigger context. Template expressions can reference its values. |

Every automation payload automatically accepts token interpolation. Strings
containing `{{ token }}` expressions are replaced with values from the trigger
context and built-in system variables before the message is queued. Tokens can
address nested fields (for example `{{ ticket.requester.email }}`) and the
special system variables documented in the automation builder.

## Execution behaviour

When an automation queues the SMTP Relay action the service records an outgoing
webhook event for monitoring. The event payload captures the resolved subject,
recipients, and bodies so administrators can audit delivery attempts. The
module then attempts to send the email immediately via the configured SMTP
server. Delivery failures are logged against the webhook event to support retry
investigations.

## Defaults and fallbacks

- If no explicit sender is provided in the payload, the module uses the
  `from_address` configured in the module settings. When that is empty the
  underlying SMTP helper falls back to the authenticated SMTP user address.
- Passing an empty `recipients` array results in the `default_recipients`
  setting being used instead.
- The automation payload can safely omit optional fields; the module always
  supplies sensible defaults so the action completes with minimal configuration.

These rules ensure existing automations continue to work while still allowing
fine-grained control when a workflow needs custom recipients, subjects, or body
content.
