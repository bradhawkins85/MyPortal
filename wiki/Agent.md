# MyPortal Agent

The MyPortal agent provides a permission-aware assistant that searches knowledge base articles, tickets requested or watched by the signed-in user, and available hardware or product recommendations. Responses are generated through the Ollama integration module, and the agent never surfaces content outside of the user's entitlements.

## Prerequisites

1. **Ollama runtime** – Install and expose an Ollama instance reachable by the application server. The default configuration expects `http://127.0.0.1:11434`.
2. **Portal configuration** – Ensure the following environment variables are defined (already included in `.env.example`):
   - `OLLAMA_BASE_URL` – HTTP(S) endpoint for your Ollama host.
   - `OLLAMA_MODEL` – Default model name (for example `llama3`).
3. **Database connectivity** – Run migrations through the existing startup process so the `tickets`, `knowledge_base`, and `shop` tables are available. SQLite fallback is supported when MySQL credentials are not configured.
4. **Webhook monitoring** – The agent records Ollama requests in the webhook monitor (`/admin/webhooks`). Ensure the monitoring tables are migrated and accessible to administrators.

## Enabling the agent

1. Sign in as a super administrator and navigate to **Admin → Modules**.
2. Locate the **Ollama** module, configure the base URL, model, and prompt if required, and set the module to **Enabled**.
3. The change takes effect immediately; no service restart is required. The install (`scripts/install_production.sh`) and upgrade (`scripts/upgrade.sh`) scripts already provision the integration module catalogue during deployment.
4. Verify connectivity from the webhook monitor. Each agent request registers a manual webhook event named `module.ollama.generate`. Failed events are retried by the existing monitoring service.

## Using the agent

- The dashboard now includes an **Agent** panel above the existing overview cards. Enter a question such as _"How do I reset the VPN appliance?"_ to search for matching knowledge base articles, your own tickets, and relevant product recommendations.
- Results show a structured answer (rendered in Markdown-style text) followed by cited sources. Source identifiers use `[KB:slug]`, `[Ticket:#id]`, or `[Product:SKU]` markers.
- The agent only operates on data available to the current user:
  - Knowledge base visibility is enforced through the existing permission scopes.
  - Tickets are limited to records requested by or watched by the user and scoped to their accessible companies.
  - Products are retrieved only when the user or their company memberships allow shop access.
- Responses include the Ollama model used, the UTC generation timestamp (rendered in the user's local timezone via the browser), and the webhook event identifier for auditing.

## API endpoint

The agent can be accessed programmatically through the `/api/agent/query` endpoint.

```http
POST /api/agent/query
Content-Type: application/json

{
  "query": "Summarise my open firewall tickets"
}
```

Successful responses return:

```json
{
  "query": "Summarise my open firewall tickets",
  "status": "succeeded",
  "answer": "…",
  "model": "llama3",
  "event_id": 4182,
  "generated_at": "2025-01-07T10:52:00.000000+00:00",
  "sources": {
    "knowledge_base": [
      {
        "slug": "network-hardening",
        "title": "Network hardening checklist",
        "summary": "",
        "excerpt": "",
        "updated_at": "2024-12-11T08:30:00Z",
        "url": "/knowledge-base/articles/network-hardening"
      }
    ],
    "tickets": [
      {
        "id": 512,
        "subject": "Firewall throughput alerts",
        "status": "open",
        "priority": "high",
        "summary": "",
        "updated_at": "2025-01-06T23:15:12+00:00"
      }
    ],
    "products": []
  },
  "context": {
    "companies": [
      { "company_id": 42, "company_name": "Contoso" }
    ]
  }
}
```

HTTP `401` is returned when the caller is unauthenticated. If the Ollama module is disabled the response status is `skipped` and the payload only contains the contextual sources.

## Security notes

- The agent never forwards raw credentials or third-party data to Ollama. Only sanitized snippets from authorised portal entities are included in the prompt.
- When no accessible context is found the agent instructs Ollama to explain that no answer is available, avoiding hallucinated responses.
- Responses are rendered as escaped HTML in the browser, ensuring that Markdown returned by Ollama cannot inject unsafe tags.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| Agent status reads *"The Ollama module is disabled"* | Enable the Ollama module or verify the configuration in **Admin → Modules**. |
| Agent returns *"Unable to contact the agent"* | Check network reachability to the Ollama host and review webhook events for failures. |
| Products do not appear as sources | Confirm the user’s company membership has `can_access_shop`, `can_access_cart`, or `can_access_orders` enabled. |

For persistent issues consult the webhook monitor (`/admin/webhooks`) or the application logs generated by the systemd service created during installation.
