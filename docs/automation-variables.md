# Ticket automation variables

Ticket-based automations receive a `ticket` object inside the event context as
well as pre-rendered template tokens. The context now includes relationship
metadata so filters and actions can target the correct recipients without manual
lookups.

## Filterable context fields

Use dotted paths in automation filters to reference specific ticket fields. Key
paths now available include:

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

Arrays such as `ticket.watchers` can be indexed numerically. For example,
matching `ticket.watchers[0].email` allows a workflow to react when the first
watcher is a specific address.

## Template tokens

During action execution every system variable is flattened into uppercase tokens
that can be interpolated inside payloads (for example when sending outbound
emails or webhook requests). Newly added tokens include:

| Token | Description |
| --- | --- |
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

All tokens resolve to empty strings when the underlying value is missing, so
payloads can safely reference them without additional guards.
