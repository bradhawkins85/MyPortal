# ChatGPT MCP Integration

The ChatGPT Model Context Protocol (MCP) module allows ChatGPT to interact with
MyPortal tickets using a secure JSON-RPC endpoint. This document expands on the
high-level notes in the README and provides configuration guidance, payload
schemas, and troubleshooting tips.

## Endpoint & Authentication

- **Endpoint:** `POST /api/mcp/chatgpt`
- **Authentication:** Provide the shared secret configured on the ChatGPT MCP
  module as a `Bearer` token. Example: `Authorization: Bearer <secret>`.
- **Content type:** `application/json`

Requests follow the JSON-RPC 2.0 format. Every request must include a `method`
field and may supply an `id` so responses can be matched to requests.

```
{
  "jsonrpc": "2.0",
  "id": "abc123",
  "method": "listTools"
}
```

## Supported Methods

### `initialize`

Returns metadata describing the MCP server.

```
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize"
}
```

**Response:**

```
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "name": "MyPortal Tickets MCP",
    "version": "1.0.0",
    "capabilities": {
      "tools": true,
      "resources": false
    }
  }
}
```

### `listTools`

Lists the tools enabled in the module configuration.

```
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "listTools"
}
```

**Response:**

```
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "listTickets",
        "description": "Return recent tickets filtered by status, company, assignee, or module.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "status": {"type": "string"},
            "company_id": {"type": "integer"},
            "assigned_user_id": {"type": "integer"},
            "module_slug": {"type": "string"},
            "search": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}
          }
        }
      },
      { "name": "getTicket", "description": "..." }
    ]
  }
}
```

### `callTool`

Invokes one of the enabled tools. `params.name` selects the tool and
`params.arguments` carries the payload.

```
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "callTool",
  "params": {
    "name": "listTickets",
    "arguments": {
      "status": "open",
      "limit": 10
    }
  }
}
```

**Response:**

```
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "json",
        "data": {
          "limit": 10,
          "tickets": [
            {
              "id": 42,
              "subject": "Printer offline",
              "status": "open",
              "priority": "normal",
              "updated_at": "2025-11-29T12:10:00+00:00"
            }
          ]
        }
      }
    ]
  }
}
```

## Tool Reference

| Tool | Description | Arguments |
| --- | --- | --- |
| `listTickets` | Returns recent tickets sorted by `updated_at`. | `status`, `module_slug`, `company_id`, `assigned_user_id`, `search`, `limit` |
| `getTicket` | Fetches a single ticket plus replies and watchers. | `ticket_id` (required) |
| `createTicketReply` | Appends a reply. Requires the module `system_user_id` or an `author_id` argument. | `ticket_id` (required), `body` (required), `is_internal`, `author_id` |
| `updateTicket` | Updates ticket status, priority, assignment, and metadata when `allow_ticket_updates` is enabled. | `ticket_id` (required) plus any of `status`, `priority`, `assigned_user_id`, `category`, `module_slug` |

## Error Handling

- **401** – Missing or invalid bearer token.
- **403** – Tool disabled or updates blocked in the module configuration.
- **404** – Referenced ticket could not be found.
- **429/503** – Module disabled or incomplete configuration (e.g. missing shared secret).

Errors are returned as standard FastAPI `HTTPException` payloads with a JSON
`detail` string.

## Troubleshooting

- Confirm the ChatGPT MCP module is enabled and that a shared secret has been
  generated. Running the **Run test** action on the admin page validates the
  configuration.
- Ensure your ChatGPT MCP configuration matches the portal URL (including
  HTTPS and port) and that the bearer token is identical to the generated
  secret.
- Ticket updates require both the `updateTicket` tool to be enabled and the
  `Allow ChatGPT to update tickets` toggle set. Without both, update attempts
  return `403`.
- When posting replies, set the system user ID to an existing user so audit
  trails remain accurate. You can override the author per request using the
  `author_id` argument.
