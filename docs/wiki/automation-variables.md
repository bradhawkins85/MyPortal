# Ticket automation and notification variables

Ticket-based automations and notifications can access a `ticket` object inside 
the event context as well as pre-rendered template tokens. The context includes 
relationship metadata so filters and actions can target the correct recipients 
without manual lookups.

## Usage in automations vs notifications

- **Automations**: Receive the full ticket context directly in the `tickets.created`, 
  `tickets.updated`, and other ticket events
- **Notifications**: When `emit_notification` is called with ticket data in the 
  `metadata` parameter, the ticket is automatically exposed at the top level of 
  the context for template rendering and module actions

## Filterable context fields

Use dotted paths in automation filters to reference specific ticket fields. Key
paths now available include:

- `ticket.number` / `ticket.ticket_number` - Ticket number (e.g., "TKT-123")
- `ticket.requester.email` / `ticket.requester.display_name`
- `ticket.assigned_user.email` / `ticket.assigned_user.display_name`
- `ticket.company.name`
- `ticket.category`, `ticket.priority`, and `ticket.external_reference`
- `ticket.ai_tags` for AI-generated categorisation
- `ticket.watchers_count` for the number of subscribed users
- `ticket.watchers[0].email` (and subsequent indexes) for individual watcher
  details
- `ticket.latest_reply.body` and `ticket.latest_reply.author_email` for the most
  recent conversation entry
- `ticket_update.actor_type` to identify whether the update originated from the
  system, an automation, the requester, a watcher, or a technician
- `ticket_update.actor_user_email` / `ticket_update.actor_user_display_name`
  expose the email and display name of the updater when available

## Ticket update actors

Ticket automations include metadata that classifies who performed an update so
filters can react to specific sources. The following actor types are emitted:

| Actor type | Description |
| --- | --- |
| `system` | Updates applied by the platform (for example AI summaries). |
| `automation` | Changes triggered by configured automation modules or imports. |
| `requester` | Updates performed by the ticket requester. |
| `watcher` | Updates performed by a user watching the ticket but not assigned. |
| `technician` | Updates performed by helpdesk staff within the portal or API. |

Arrays such as `ticket.watchers` can be indexed numerically. For example,
matching `ticket.watchers[0].email` allows a workflow to react when the first
watcher is a specific address.

## Template tokens

During action execution every system variable is flattened into uppercase tokens
that can be interpolated inside payloads (for example when sending outbound
emails or webhook requests). Newly added tokens include:

| Token | Description |
| --- | --- |
| `{{ TICKET_NUMBER }}` | Ticket number (e.g., "TKT-123"). Also available as `{{ TICKET_TICKET_NUMBER }}`. |
| `{{ TICKET_REQUESTER_EMAIL }}` | Requester email address. |
| `{{ TICKET_REQUESTER_DISPLAY_NAME }}` | Requester display name derived from their profile. |
| `{{ TICKET_ASSIGNED_USER_EMAIL }}` | Assigned technician email address. |
| `{{ TICKET_ASSIGNED_USER_DISPLAY_NAME }}` | Assigned technician display name. |
| `{{ TICKET_COMPANY_NAME }}` | Linked company name (also exposed as `{{ COMPANY_NAME }}`). |
| `{{ TICKET_CATEGORY }}` / `{{ TICKET_PRIORITY }}` | Ticket routing metadata. |
| `{{ TICKET_EXTERNAL_REFERENCE }}` | External system identifier such as an RMM ticket number. |
| `{{ TICKET_AI_TAGS_0 }}` ... | AI-generated tags (indexed for each tag). |
| `{{ TICKET_WATCHERS_COUNT }}` | Number of watchers subscribed to the ticket. |
| `{{ TICKET_WATCHERS_0_EMAIL }}` ... | Indexed watcher email addresses. |
| `{{ TICKET_WATCHERS_0_USER_EMAIL }}` ... | Indexed watcher user profile email values. |
| `{{ TICKET_WATCHER_EMAILS_0 }}` ... | Flattened list of watcher emails for quick iteration. |
| `{{ TICKET_LATEST_REPLY_BODY }}` | Body of the most recent reply. |
| `{{ TICKET_LATEST_REPLY_AUTHOR_EMAIL }}` | Email for the author of the latest reply. |
| `{{ TICKET_LATEST_REPLY_AUTHOR_DISPLAY_NAME }}` | Display name for the latest reply author. |
| `{{ ACTIVE_ASSETS }}` / `{{ ACTIVE_ASSETS:7 }}` | Count of assets that synced in the current month, or within the last `N` days when the `:N` suffix is provided. |
| `{{ count:asset:field-name }}` | Count of assets with a specific custom field checkbox set to true (e.g., `{{ count:asset:bitdefender }}` or `{{ count:asset:threatlocker-installed }}`). |
| `{{ list:asset:field-name }}` | Comma-separated list of asset names with a specific custom field checkbox set to true (e.g., `{{ list:asset:bitdefender }}`). |
| `{{ count:issue:slug }}` | Count of assets linked to a specific issue type (e.g., `{{ count:issue:network-outage }}`). |
| `{{ list:issue:slug }}` | Comma-separated list of asset names linked to a specific issue type (e.g., `{{ list:issue:network-outage }}`). |

`{{ ACTIVE_ASSETS }}` tokens scope to the company in the current automation context when available (for example a ticket's company). When no company is present the counts cover the entire tenant. The default form returns assets with a Syncro sync timestamp in the current month; append `:N` to evaluate the past `N` days such as `{{ ACTIVE_ASSETS:1 }}` for the past day.

### Asset custom field count variables

The `{{ count:asset:field-name }}` variables allow you to count assets that have a specific checkbox custom field set to true. These variables are particularly useful for tracking security software installations, compliance status, or any other asset characteristic tracked via checkbox custom fields.

**Usage examples:**

- `{{ count:asset:bitdefender }}` - Count assets with the "bitdefender" checkbox enabled
- `{{ count:asset:threatlocker-installed }}` - Count assets with the "threatlocker-installed" checkbox enabled
- `{{ count:asset:warranty-active }}` - Count assets with the "warranty-active" checkbox enabled

**Context scoping:**

Like `ACTIVE_ASSETS`, these counters automatically scope to the company associated with the current ticket when used in ticket automations. When no company context is available, they count across all companies in the tenant.

**Field name matching:**

The field name in the variable (after `count:asset:`) must match the exact name of a checkbox custom field defined in **Admin → Asset Custom Fields**. The comparison is case-sensitive and the field must be of type "checkbox".

**Example automation use case:**

```
Subject: Security Software Status for {{ COMPANY_NAME }}
Body: You have {{ count:asset:bitdefender }} assets with Bitdefender and {{ count:asset:threatlocker-installed }} assets with ThreatLocker installed.
```

### Asset custom field list variables

The `{{ list:asset:field-name }}` variables return a comma-separated list of asset names that have a specific checkbox custom field set to true. These work similarly to the count variables but provide the actual asset names instead of just a count.

**Usage examples:**

- `{{ list:asset:bitdefender }}` - List assets with the "bitdefender" checkbox enabled
- `{{ list:asset:threatlocker-installed }}` - List assets with the "threatlocker-installed" checkbox enabled

**Context scoping and field matching:**

List variables follow the same scoping and field name matching rules as count variables. The assets are returned as a comma-separated list (e.g., "Server-01, Server-02, Workstation-03"). When no assets match, an empty string is returned.

### Conditional logic

Template variables support conditional expressions to dynamically return different values based on conditions. This is particularly useful for showing asset lists only when they exist, providing different messages based on counts, or any other conditional logic.

**Syntax:**

```
{{ if condition then value_if_true else value_if_false }}
```

The `else` clause is optional. If omitted and the condition is false, an empty string is returned.

**Comparison operators:**

Conditional expressions support the following comparison operators:
- `>` - Greater than
- `<` - Less than
- `>=` - Greater than or equal to
- `<=` - Less than or equal to
- `==` - Equal to
- `!=` - Not equal to

**Example use cases:**

Show asset list only when assets exist:
```
Bitdefender assets: {{ if count:asset:bitdefender > 0 then list:asset:bitdefender else "None installed" }}
```

Display different messages based on count:
```
{{ if count:asset:bitdefender >= 10 then "Many assets protected" else "Few assets protected" }}
```

Conditional subject line:
```
Subject: {{ if count:asset:bitdefender > 0 then "Action Required: Bitdefender Updates" else "No action needed" }}
```

Multiple conditionals in the same template:
```
Security Status:
- Bitdefender: {{ if count:asset:bitdefender > 0 then list:asset:bitdefender else "Not installed" }}
- ThreatLocker: {{ if count:asset:threatlocker-installed > 0 then list:asset:threatlocker-installed else "Not installed" }}
```

**Notes:**

- The `if`, `then`, and `else` keywords are case-insensitive
- Variable names in conditionals (like `count:asset:bitdefender`) are automatically detected and resolved
- Numeric comparisons are performed when both sides can be converted to numbers
- String comparisons are used as a fallback when numeric conversion fails
- Empty strings and zero values are considered "false" in boolean contexts

## Message templates

Reusable snippets such as email bodies or webhook payloads can be defined as
message templates. Reference a template anywhere variables are supported using
either the uppercase or dotted token form:

- `{{ TEMPLATE_WELCOME_EMAIL }}`
- `{{ template.welcome_email }}`

Template content itself may include other variables like `{{ ticket.id }}` or
`{{ APP_NAME }}`. During automation execution the template is rendered with the
same context as the surrounding payload before being substituted.

All tokens resolve to empty strings when the underlying value is missing, so
payloads can safely reference them without additional guards.
