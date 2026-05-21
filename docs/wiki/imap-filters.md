# IMAP mailbox filters

Mailbox filters let you route emails to the correct automation path when the same mailbox is connected multiple times. Filters
are stored as JSON objects that describe which messages should be imported. When a filter is omitted or empty, every message is
processed.

## Supported fields

Filters evaluate against a context that exposes the following keys:

- `subject`: The decoded email subject line.
- `body`: The plain-text or HTML body extracted from the message.
- `from.addresses`: A list of sender email addresses.
- `from.address`: The first sender email address.
- `from.domains`: A list of sender domains.
- `from.domain`: The first sender domain.
- `to`: A list of primary recipient email addresses.
- `cc`: A list of CC recipient email addresses.
- `bcc`: A list of BCC recipient email addresses.
- `to_domains` and `cc_domains`: The recipient domains for To / CC headers.
- `reply_to.addresses`: Addresses from the Reply-To header (if present).
- `sender.addresses`: Addresses from the Sender header (if present).
- `mailbox.folder`: The IMAP folder that was synced.
- `is_unread` / `is_read`: Whether the message was unread when fetched.
- `flags`: The IMAP flags returned by the server (for example `"\\Seen"`).
- `message_id`: The RFC 822 Message-ID.
- `headers`: A map of all message headers using lower-case keys.
- `account.id`, `account.name`, `account.company_id`: Metadata for the mailbox configuration.

## Operators

Each condition defines a `field` and one operator:

- Equality: `equals`, `not_equals`
- List membership: `in`, `not_in`
- String search: `contains`, `not_contains`, `starts_with`, `ends_with`
- Regular expressions: `matches`, `not_matches`
- Presence checks: `present`, `absent`

All string operators are case-insensitive by default. Set `"case_sensitive": true` to enable case-sensitive comparisons. Groups
can be composed with `all`, `any`, and `none` keys, each containing an array of nested rules.

## Examples

### 1. Match a single sender domain
```json
{"field": "from.domain", "equals": "example.com"}
```

### 2. Allow multiple sender domains
```json
{"field": "from.domain", "in": ["example.com", "example.org"]}
```

### 3. Import only unread messages
```json
{"field": "is_unread", "equals": true}
```

### 4. Require messages from a specific folder
```json
{"field": "mailbox.folder", "equals": "Support"}
```

### 5. Match subjects that start with a prefix
```json
{"field": "subject", "starts_with": "[High Priority]"}
```

### 6. Match subjects using a case-insensitive regex
```json
{"field": "subject", "matches": "(?i)^ticket\\s+#\\d+"}
```

### 7. Ignore monitoring noise with a negative regex
```json
{"field": "subject", "not_matches": "(?i)heartbeat"}
```

### 8. Inspect the message body for keywords
```json
{"field": "body", "contains": "remote access"}
```

### 9. Require HTML reports by checking for a table tag
```json
{"field": "body", "matches": "<table[\\s>].*</table>"}
```

### 10. Match a dedicated sender address
```json
{"field": "from.address", "equals": "alerts@example.net"}
```

### 11. Combine subject and sender checks
```json
{
  "all": [
    {"field": "from.domain", "equals": "example.com"},
    {"field": "subject", "contains": "outage"}
  ]
}
```

### 12. Accept messages from two different teams
```json
{
  "any": [
    {"field": "from.address", "equals": "team-a@example.com"},
    {"field": "from.address", "equals": "team-b@example.com"}
  ]
}
```

### 13. Reject marketing emails while allowing other traffic
```json
{
  "all": [
    {"field": "from.domain", "equals": "vendor.com"},
    {"none": [
      {"field": "subject", "matches": "(?i)newsletter"},
      {"field": "subject", "matches": "(?i)webinar"}
    ]}
  ]
}
```

### 14. Match tickets that mention a priority client in the body or subject
```json
{
  "any": [
    {"field": "subject", "matches": "(?i)acme corp"},
    {"field": "body", "matches": "(?i)acme corp"}
  ]
}
```

### 15. Require the Reply-To header to be present
```json
{"field": "reply_to.addresses", "present": true}
```

### 16. Skip messages that already have the \"Seen\" flag
```json
{"field": "flags", "not_contains": "\\\Seen"}
```

### 17. Filter by company-specific sender list
```json
{"field": "from.addresses", "in": ["support@partner.com", "support@reseller.com"]}
```

### 18. Allow only messages that CC an escalation queue
```json
{"field": "cc", "contains": "escalations@example.com"}
```

### 19. Require the Message-ID header to include a known prefix
```json
{"field": "message_id", "starts_with": "<srv-"}
```

### 20. Combine folder, domain, and unread status checks
```json
{
  "all": [
    {"field": "mailbox.folder", "equals": "VIP"},
    {"field": "from.domain", "equals": "example.com"},
    {"field": "is_unread", "equals": true}
  ]
}
```

### 21. Require tickets addressed to a shared mailbox but exclude marketing
```json
{
  "all": [
    {"field": "to", "contains": "helpdesk@example.com"},
    {"field": "subject", "not_matches": "(?i)promotion"}
  ]
}
```

### 22. Match service outages that mention \"critical\" in the body and arrive from specific senders
```json
{
  "all": [
    {"field": "from.addresses", "in": ["ops@monitoring.com", "alerts@monitoring.com"]},
    {"field": "body", "matches": "(?i)critical"}
  ]
}
```

### 23. Only import emails that have no CC recipients
```json
{"field": "cc", "absent": true}
```

### 24. Require either a matching sender or a matching Reply-To domain
```json
{
  "any": [
    {"field": "from.domain", "equals": "client.com"},
    {"field": "reply_to.addresses", "matches": "@client.com$"}
  ]
}
```

### 25. Allow invoices from finance while blocking duplicate threads
```json
{
  "all": [
    {"field": "from.address", "equals": "finance@example.com"},
    {"field": "subject", "not_contains": "Re:"}
  ]
}
```
