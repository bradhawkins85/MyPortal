# API key management

Super administrators can issue API keys for service integrations via the **Admin → API credentials**
dashboard or programmatically through the `/api/api-keys` endpoints. Keys may now be scoped to
specific HTTP methods on individual endpoints, allowing fine-grained access control for downstream
systems.

## Defining endpoint permissions

Each API key can optionally include a `permissions` collection. When present, the key is limited to
the listed path templates and HTTP methods. Leave the collection empty (or omit it entirely) to
grant unrestricted access.

Example configuration:

```json
{
  "permissions": [
    { "path": "/api/orders", "methods": ["GET"] },
    { "path": "/api/orders/{orderNumber}", "methods": ["GET", "PATCH"] }
  ]
}
```

Path values must match the FastAPI route template (including parameter placeholders). Supported
methods are `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS`.

## Restricting source IP addresses

API keys can also be constrained to a curated set of source IP addresses or CIDR ranges by supplying
an `allowed_ips` array. Each entry accepts a single IP address (IPv4 or IPv6) or a network in CIDR
notation. Requests originating outside the allow list receive `403 Forbidden` responses even when the
API key is otherwise valid.

```json
{
  "allowed_ips": [
    { "cidr": "203.0.113.42/32" },
    { "cidr": "2001:db8::/48" }
  ]
}
```

Omitting the field (or leaving it empty) preserves the previous behaviour and accepts traffic from
any IP address. The admin console automatically normalises individual hosts to their equivalent
`/32` (IPv4) or `/128` (IPv6) networks and prevents duplicate entries.

## Admin console workflow

The create and rotate forms in the API credentials dashboard accept one entry per line in the
`METHOD /path` format, for example:

```
GET /api/orders
GET,POST /api/orders/{orderNumber}
```

Lines may contain multiple comma-separated methods targeting the same path. Leaving the field blank
produces an unrestricted credential.

When viewing an existing key, the detail modal displays its current endpoint permissions and allows
rotating the credential while editing the list. Keys without any explicit permissions are marked as
"Unrestricted" in the overview table.

The same modal now surfaces the configured source IP allow list. Enter one address or CIDR range per
line in the "Source IP allow list" field when creating or rotating a key. The overview table displays
both endpoint and IP summaries so administrators can quickly identify restricted credentials.

## API schema updates

The following REST endpoints now accept and return the `permissions` field:

- `POST /api/api-keys`
- `GET /api/api-keys`
- `GET /api/api-keys/{id}`
- `POST /api/api-keys/{id}/rotate`

Each response includes the resolved permissions and allowed IPs for the key, ensuring API consumers
can audit the configured scope. During rotation, omitting either field retains the previous
configuration, providing backward compatibility with existing automation.
