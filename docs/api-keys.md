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

## API schema updates

The following REST endpoints now accept and return the `permissions` field:

- `POST /api/api-keys`
- `GET /api/api-keys`
- `GET /api/api-keys/{id}`
- `POST /api/api-keys/{id}/rotate`

Each response includes the resolved permissions for the key, ensuring API consumers can audit the
configured scope. During rotation, omitting the field retains the previous configuration, providing
backward compatibility with existing automation.
