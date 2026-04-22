# Model Context Protocol (MCP) Server

The MCP WebSocket server provides authorized agents (such as GitHub Copilot) with secure, read-only access to live application data over a WebSocket connection.

## Overview

The MCP server is a feature-flagged WebSocket endpoint that:
- Requires token authentication for all connections
- Provides read-only access to a whitelist of data models
- Automatically filters sensitive fields from responses
- Enforces per-connection rate limiting
- Is disabled by default for security

## Configuration

Configure the MCP server via environment variables in your `.env` file:

```bash
# Enable the MCP WebSocket server (default: false)
MCP_ENABLED=false

# Secret authentication token (required if MCP_ENABLED is true)
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
MCP_TOKEN=your-secret-token-here

# Comma-separated list of allowed models (default: users,tickets,change_log)
MCP_ALLOWED_MODELS=users,tickets,change_log

# Enforce read-only operations (default: true)
MCP_READONLY=true

# Maximum requests per minute per connection (default: 60)
MCP_RATE_LIMIT=60
```

### Generating a Secure Token

Generate a secure random token for `MCP_TOKEN`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Important:** Keep this token secret. Anyone with this token can access your application data.

## WebSocket Endpoint

Once enabled, the MCP server is available at:

```
ws://your-domain.com/mcp/ws
wss://your-domain.com/mcp/ws  (for HTTPS/WSS)
```

## Authentication

Provide the authentication token in one of two ways:

### 1. HTTP Header (Recommended)

```javascript
const ws = new WebSocket('ws://localhost:8000/mcp/ws', {
    headers: {
        'X-MCP-Token': 'your-secret-token-here'
    }
});
```

### 2. Query Parameter

```javascript
const ws = new WebSocket('ws://localhost:8000/mcp/ws?token=your-secret-token-here');
```

**Note:** The header method is preferred for better security as query parameters may be logged.

## Message Format

### Request

Send JSON messages with this structure:

```json
{
    "id": "unique-request-id",
    "action": "list|get|query",
    "model": "users|tickets|change_log",
    "params": {
        // Action-specific parameters
    }
}
```

### Response

All responses include the original request `id` and a `status`:

```json
{
    "id": "unique-request-id",
    "status": "ok|error",
    "data": { /* response data */ },
    "error": "error message if status is error"
}
```

## Supported Actions

### 1. List Records

Retrieve a paginated list of records from a model.

**Request:**
```json
{
    "id": "list-users-1",
    "action": "list",
    "model": "users",
    "params": {
        "limit": 50,
        "offset": 0,
        "filters": {
            "is_super_admin": true
        }
    }
}
```

**Response:**
```json
{
    "id": "list-users-1",
    "status": "ok",
    "data": [
        {
            "id": 1,
            "email": "admin@example.com",
            "first_name": "Admin",
            "last_name": "User"
            // Note: password_hash and other sensitive fields are automatically filtered
        }
    ],
    "count": 1,
    "limit": 50,
    "offset": 0
}
```

**Parameters:**
- `limit` (optional): Maximum records to return (default: 50, max: 100)
- `offset` (optional): Pagination offset (default: 0)
- `filters` (optional): Dictionary of field=value equality filters

### 2. Get Single Record

Retrieve a single record by ID.

**Request:**
```json
{
    "id": "get-user-1",
    "action": "get",
    "model": "users",
    "params": {
        "id": 1
    }
}
```

**Response:**
```json
{
    "id": "get-user-1",
    "status": "ok",
    "data": {
        "id": 1,
        "email": "admin@example.com",
        "first_name": "Admin",
        "last_name": "User"
    }
}
```

**Parameters:**
- `id` (required): Record ID to retrieve

### 3. Query Records

Similar to list but intended for more expressive filtering (currently uses same implementation as list for security).

**Request:**
```json
{
    "id": "query-tickets-1",
    "action": "query",
    "model": "tickets",
    "params": {
        "filters": {
            "status": "open",
            "priority": "high"
        },
        "limit": 10
    }
}
```

**Response:**
```json
{
    "id": "query-tickets-1",
    "status": "ok",
    "data": [ /* filtered ticket records */ ],
    "count": 5,
    "limit": 10,
    "offset": 0
}
```

## Security Features

### 1. Disabled by Default

The MCP server must be explicitly enabled via `MCP_ENABLED=true`. This prevents accidental exposure in production.

### 2. Token Authentication

All connections require a valid authentication token. Connections without a token or with an invalid token are immediately rejected.

### 3. Read-Only Operations

By default (`MCP_READONLY=true`), only read operations are allowed:
- `list`
- `get`
- `query`

Any write operations (create, update, delete) are rejected with an error.

### 4. Model Whitelist

Only models explicitly listed in `MCP_ALLOWED_MODELS` can be accessed. Requests for other models are rejected.

Default allowed models:
- `users`
- `tickets`
- `change_log`

### 5. Sensitive Field Filtering

The following field types are automatically filtered from all responses:
- `password_hash`, `password`
- `secret`, `token`, `api_key`
- `totp_secret`, `encryption_key`
- `private_key`, `client_secret`
- `webhook_secret`, `auth_token`

These fields are removed before sending any data to the client.

### 6. Rate Limiting

Each connection is rate-limited to prevent abuse:
- Default: 60 requests per minute
- Configurable via `MCP_RATE_LIMIT`
- Violations close the connection

### 7. Simple Filtering Only

For security, only simple equality filters are supported:
```json
{
    "filters": {
        "field_name": "exact_value"
    }
}
```

Complex queries, SQL injection attempts, or other sophisticated filtering is not supported.

## Usage Examples

### Python Example

```python
import asyncio
import json
import websockets

async def query_mcp():
    uri = "ws://localhost:8000/mcp/ws"
    headers = {"X-MCP-Token": "your-secret-token"}
    
    async with websockets.connect(uri, extra_headers=headers) as websocket:
        # List users
        request = {
            "id": "req-1",
            "action": "list",
            "model": "users",
            "params": {"limit": 10}
        }
        await websocket.send(json.dumps(request))
        response = json.loads(await websocket.recv())
        print(f"Users: {response['data']}")
        
        # Get specific ticket
        request = {
            "id": "req-2",
            "action": "get",
            "model": "tickets",
            "params": {"id": 123}
        }
        await websocket.send(json.dumps(request))
        response = json.loads(await websocket.recv())
        print(f"Ticket: {response['data']}")

asyncio.run(query_mcp())
```

### JavaScript Example

```javascript
const ws = new WebSocket('ws://localhost:8000/mcp/ws', {
    headers: {
        'X-MCP-Token': 'your-secret-token'
    }
});

ws.onopen = () => {
    // List open tickets
    const request = {
        id: 'req-1',
        action: 'list',
        model: 'tickets',
        params: {
            filters: { status: 'open' },
            limit: 20
        }
    };
    ws.send(JSON.stringify(request));
};

ws.onmessage = (event) => {
    const response = JSON.parse(event.data);
    console.log('Response:', response);
    
    if (response.status === 'ok') {
        console.log('Data:', response.data);
    } else {
        console.error('Error:', response.error);
    }
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};

ws.onclose = () => {
    console.log('Connection closed');
};
```

## Error Handling

Common error responses:

### Invalid Token
```json
{
    "status": "error",
    "error": "Invalid authentication token"
}
```
Connection is immediately closed.

### Model Not Allowed
```json
{
    "id": "req-1",
    "status": "error",
    "error": "Model 'sensitive_data' not allowed. Allowed models: users, tickets, change_log"
}
```

### Write Operation in Read-Only Mode
```json
{
    "id": "req-1",
    "status": "error",
    "error": "Write operations not allowed in read-only mode: create"
}
```

### Rate Limit Exceeded
```json
{
    "status": "error",
    "error": "Rate limit exceeded: 60 requests per 60s"
}
```
Connection is closed after this error.

### Invalid Request
```json
{
    "id": "req-1",
    "status": "error",
    "error": "Missing required parameter: id"
}
```

## Monitoring and Logging

The MCP server logs important events:
- Connection established/closed
- Authentication failures
- Rate limit violations
- Query errors

Check application logs for MCP-related events:
```bash
# Example log entries
INFO: MCP WebSocket connection established
WARNING: MCP connection rejected: Invalid authentication token
WARNING: MCP connection closed: Rate limit exceeded
ERROR: MCP list query failed for model users: ...
```

## Best Practices

1. **Secure Token Management**
   - Generate strong random tokens
   - Store tokens securely (environment variables, secret management)
   - Rotate tokens periodically
   - Never commit tokens to version control

2. **Production Deployment**
   - Use HTTPS/WSS in production
   - Set appropriate `MCP_RATE_LIMIT` based on expected load
   - Monitor MCP usage and errors
   - Keep `MCP_READONLY=true` unless writes are absolutely necessary

3. **Model Access**
   - Only include models in `MCP_ALLOWED_MODELS` that are safe to expose
   - Avoid models containing highly sensitive data
   - Review model schemas to ensure sensitive fields are properly filtered

4. **Network Security**
   - Use firewall rules to restrict WebSocket access if possible
   - Consider IP whitelisting for additional security
   - Use VPN or private networks for sensitive deployments

## Troubleshooting

### Connection Rejected Immediately

**Cause:** Invalid or missing authentication token

**Solution:** 
- Verify `MCP_TOKEN` is set in `.env`
- Ensure token matches between server and client
- Check header name is exactly `X-MCP-Token` or use query param `?token=`

### "MCP server is disabled" Error

**Cause:** MCP is not enabled

**Solution:**
- Set `MCP_ENABLED=true` in `.env`
- Restart the application
- Verify settings with `python -c "from app.core.config import get_settings; print(get_settings().mcp_enabled)"`

### Rate Limit Errors

**Cause:** Too many requests in the rate limit window

**Solution:**
- Increase `MCP_RATE_LIMIT` if legitimate traffic
- Add delays between requests in client code
- Use pagination and filtering to reduce request count

### Empty Results

**Cause:** Database query returned no results or filters don't match

**Solution:**
- Verify model name is correct and in allowed list
- Check filter values match database data
- Test queries directly on database to verify data exists

## Development and Testing

For development, you can enable MCP with a simple token:

```bash
# .env (development only!)
MCP_ENABLED=true
MCP_TOKEN=dev-token-12345
MCP_ALLOWED_MODELS=users,tickets,change_log
```

Run tests:
```bash
pytest tests/test_mcp.py -v
```

The test suite covers:
- Authentication (header and query param)
- Token validation
- Read operations (list, get, query)
- Write operation rejection
- Rate limiting
- Sensitive field filtering
- Error handling

## Support

For issues or questions:
- Check application logs for error details
- Review this documentation
- Verify configuration settings
- Test with simple requests before complex ones

---

## Ollama / GitHub Copilot MCP (HTTP JSON-RPC 2.0)

MyPortal also ships a standards-compliant MCP endpoint that uses the HTTP
JSON-RPC 2.0 transport required by **GitHub Copilot**, Ollama, and other MCP
clients.

### Endpoints

| Path | Purpose |
|---|---|
| `POST /api/mcp/ollama/` | Ollama MCP endpoint |
| `POST /api/mcp/copilot/` | Dedicated GitHub Copilot endpoint (same backend) |
| `POST /api/mcp/copilot/rpc` | Alias for clients that expect `/rpc` |
| `GET  /api/mcp/copilot/manifest` | Public discovery manifest (no auth) |

### GitHub Copilot Setup

#### VS Code `settings.json`

```json
{
  "mcp": {
    "servers": {
      "myportal": {
        "type": "http",
        "url": "https://your-myportal-host/api/mcp/copilot/",
        "headers": {
          "Authorization": "Bearer <your-shared-secret>"
        }
      }
    }
  }
}
```

#### `.github/copilot-mcp.json` (repository-scoped)

```json
{
  "servers": {
    "myportal": {
      "type": "http",
      "url": "https://your-myportal-host/api/mcp/copilot/",
      "headers": {
        "Authorization": "Bearer <your-shared-secret>"
      }
    }
  }
}
```

The shared secret is the value you set in the **Ollama MCP** module settings
(`OLLAMA_MCP_SHARED_SECRET`). It is hashed before storage; the plain-text
value must be supplied in the `Authorization` header.

### Authentication

Provide your shared secret as a bearer token:

```
Authorization: Bearer <plain-text-shared-secret>
```

### Available Tools

The Copilot/Ollama endpoint exposes all tools configured in the Ollama MCP
module. The default set includes:

| Tool | Description |
|---|---|
| `search_tickets` | Free-text search across ticket subjects and descriptions |
| `list_tickets` | Browse recent tickets with optional filters |
| `get_ticket` | Full detail for one ticket including replies and watchers |
| `list_ticket_statuses` | List the configured ticket statuses |
| `search_audit_logs` | Search the audit log (see below) |
| `get_audit_log` | Retrieve one audit log entry by ID |
| `get_application_logs` | Read recent application log lines from disk |

Optional write tools (`create_ticket_reply`, `update_ticket`) must be
explicitly enabled in the module settings.

### Log Tools

The three log-review tools are gated by `MCP_LOG_TOOLS_ENABLED` (default `true`).

#### `search_audit_logs`

Search the database audit log for user and system actions. Useful for
tracing permission changes, login events, or entity modifications.

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `user_id` | integer | Filter by the user who performed the action |
| `action` | string | Partial match on action name (e.g. `login`, `delete`) |
| `entity_type` | string | Filter by entity type (e.g. `ticket`, `user`) |
| `ip_address` | string | Exact client IP filter |
| `since` | ISO-8601 datetime | Only entries on or after this time |
| `until` | ISO-8601 datetime | Only entries on or before this time |
| `search` | string | Free-text search across action, entity_type, user email |
| `limit` | integer | Max entries to return (default: module max_results) |
| `offset` | integer | Pagination offset |

**Example request:**
```json
{
  "jsonrpc": "2.0",
  "id": "al-1",
  "method": "tools/call",
  "params": {
    "name": "search_audit_logs",
    "arguments": {
      "action": "login",
      "since": "2026-01-01T00:00:00Z",
      "limit": 10
    }
  }
}
```

**Response fields:** `id`, `user_id`, `user_email`, `action`, `entity_type`,
`entity_id`, `ip_address`, `request_id`, `created_at`.  
Sensitive fields (`api_key`, `previous_value`, `new_value`, `metadata`) are
**never** included.

#### `get_audit_log`

Retrieve a single audit log entry by its numeric ID.

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `log_id` | integer (required) | Numeric ID of the audit log entry |

#### `get_application_logs`

Read recent lines from the on-disk application log (`FAIL2BAN_LOG_PATH`) or
the dedicated error log (`ERROR_LOG_PATH`). Only available when the relevant
path is configured and the file exists. Absolute file paths are never exposed.

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `lines` | integer | Lines to return from the tail (default 100, max 500) |
| `level` | `INFO`\|`WARNING`\|`ERROR` | Minimum severity filter |
| `since` | ISO-8601 datetime | Only lines on or after this time |
| `search` | string | Case-insensitive substring filter on the message |
| `log_file` | `application`\|`error` | Which log file to read (default `application`) |

**Example request:**
```json
{
  "jsonrpc": "2.0",
  "id": "log-1",
  "method": "tools/call",
  "params": {
    "name": "get_application_logs",
    "arguments": {
      "lines": 50,
      "level": "ERROR",
      "search": "database"
    }
  }
}
```

**Response fields per line:** `timestamp`, `level`, `request_id`, `user_id`, `message`.

When the log file is not configured the response includes `"available": false`
and a human-readable `message` explaining how to enable it.

### New Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_LOG_TOOLS_ENABLED` | `true` | Enable/disable all log-review MCP tools |
| `MCP_LOG_MAX_LINES` | `500` | Hard cap on lines returned by `get_application_logs` |

