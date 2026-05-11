# Solidtime integration

MyPortal can synchronise its tickets and ticket-time entries with
[Solidtime](https://github.com/solidtime-io/solidtime), an open-source
(AGPL v3) Laravel-based time-tracker.

## Concept mapping

| MyPortal                                                           | Solidtime                                                              |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Company (customer)                                                 | Client                                                                 |
| Ticket                                                             | Project (one project per ticket; name = `#<ticket_number> – <subject>`) |
| Ticket reply with `minutes_spent` (`is_billable`)                  | Time Entry on the ticket's Project (optionally a Task per labour type) |
| Labour type                                                        | Task within the Project (optional)                                     |
| User who logged the reply                                          | Solidtime member (mapped via email)                                    |

## Setup

1. **Generate a Solidtime API token.** Inside Solidtime, open *Profile → API
   tokens* and create a new personal access token with read/write scopes for
   organisations, clients, projects, tasks, members and time-entries.
2. **Open the MyPortal admin Modules page** and enable the **Solidtime**
   module. Provide:
    * **Base URL** – e.g. `https://app.solidtime.io` (the integration appends
      `/api/v1` automatically).
    * **API token** – the token from step 1. Stored encrypted at rest and
      redacted from the admin UI on subsequent loads.
    * **Organisation ID** – pick one from *Test connection*. This UUID is
      required because every Solidtime API call is scoped to an organisation.
    * Optional **default client ID** as a fallback for companies that have no
      Solidtime client mapped yet.
3. **Decide on the sync direction.** The module ships with sensible defaults:
    * Push tickets to Solidtime as projects: ✅
    * Push reply time entries to Solidtime: ✅
    * Pull time entries from Solidtime: ✅
    * Pull new Solidtime projects in as MyPortal tickets: ❌ (off by default
      to avoid noise)

## Time-entry mapping rule

A ticket reply with `minutes_spent` becomes a Solidtime time entry computed
as follows (UTC throughout):

```
end   = reply.created_at
start = end - timedelta(minutes=reply.minutes_spent)
```

`is_billable` on the reply maps to `billable` on the time entry. The first
line of the reply body is used as the time-entry description.

## Background work

* **Outbound** – ticket create/update/close and reply create/update fire
  fire-and-forget background tasks via `asyncio.create_task` so the API
  response time is not affected.
* **Inbound** – the scheduled job `solidtime-reconcile` (5-minute interval)
  pulls projects and time-entries updated since the last run. The same
  reconciler can be triggered on-demand from `POST /api/v1/solidtime/reconcile`.
* **Webhooks** – when a forwarder is configured, inbound posts to
  `POST /api/v1/solidtime/webhook` are HMAC-verified using the configured
  webhook secret.

## Per-user linking

A MyPortal user is matched to a Solidtime member by their primary email
address the first time their reply is synced. Override the auto-match by
inserting/updating a row in `solidtime_user_links`.

## Ticket detail page

Tickets that are linked to a Solidtime project show a **Solidtime** button in
the top-right of the ticket header (next to the existing Hudu link). The
button opens Solidtime's timer page pre-scoped to the linked project so
technicians can start tracking time with one click.

## Storage

Mapping is stored in four tables introduced by migration
`245_solidtime_integration.sql`:

* `solidtime_client_links` – `company_id` ↔ Solidtime client
* `solidtime_project_links` – `ticket_id` ↔ Solidtime project
* `solidtime_time_entry_links` – `ticket_reply_id` ↔ Solidtime time entry
* `solidtime_user_links` – `user_id` ↔ Solidtime member

Each link row records `last_synced_at`, `sync_status`, and `last_error` so
the reconciler can retry failed syncs without re-applying successful work.
