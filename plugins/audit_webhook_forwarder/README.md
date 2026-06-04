# Audit Webhook Forwarder plugin

This sample plugin demonstrates:

- Plugin startup/shutdown hooks
- Plugin background jobs (`background_jobs`)
- Env-var configuration (`PLUGIN_AUDIT_FORWARDER_*`)
- Outbound HTTP with `httpx`
- Reusing `app.repositories.audit_logs`

Environment variables:

- `PLUGIN_AUDIT_FORWARDER_URL` (required to send)
- `PLUGIN_AUDIT_FORWARDER_INTERVAL_SECONDS` (default `60`)
- `PLUGIN_AUDIT_FORWARDER_BATCH_SIZE` (default `50`)
