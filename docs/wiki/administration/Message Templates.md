# Message templates API

Message templates allow administrators to store reusable email or notification
content that can be interpolated anywhere automation variables are supported.
Templates accept the same variable syntax as automation payloads so bodies can
reference ticket fields, system metadata, or user profiles without duplicating
large strings across automations.

## REST endpoints

The `/api/message-templates` endpoints require super-admin privileges and are
available through the Swagger UI once authenticated.

| Method & path | Description |
| --- | --- |
| `GET /api/message-templates` | List existing templates with optional search or content-type filters. |
| `POST /api/message-templates` | Create a new template by providing a slug, name, content type, and body. |
| `GET /api/message-templates/{id}` | Retrieve a template by numeric identifier. |
| `GET /api/message-templates/slug/{slug}` | Retrieve a template by slug for quick lookups. |
| `PUT /api/message-templates/{id}` | Update the template metadata or body content. |
| `DELETE /api/message-templates/{id}` | Remove a template. |

Template slugs must be lowercase and may contain letters, numbers, dots,
hyphens, or underscores. Slugs become part of the interpolation token, for
example a slug of `welcome_email` can be referenced as `{{ TEMPLATE_WELCOME_EMAIL }}`
or `{{ template.welcome_email }}`.

## Using templates in automations

When an automation action payload contains a template token the system renders
the template body using the same execution context (ticket details, system
variables, etc.) before substituting the text into the payload. This allows
large HTML emails or structured webhook payloads to live in one place while
still picking up context-specific values like `{{ ticket.id }}` or
`{{ user.email }}`.

Templates can also be used inside areas that support template variables such as
smtp trigger bodies, SMS messages, or ntfy notifications.
