# Huntress integration

The Huntress integration pulls a daily snapshot of EDR, ITDR, Security
Awareness Training, Managed SIEM, and SOC statistics for each linked company
and surfaces them in the Company Overview report.

## Enabling the module

1. Generate an API key + secret in your Huntress portal under
   **Account → API**.
2. Set the following variables in the host's `.env` file:

   ```
   HUNTRESS_API_KEY=...
   HUNTRESS_API_SECRET=...
   HUNTRESS_BASE_URL=https://api.huntress.io/v1   # optional
   ```

3. Restart MyPortal so the new settings are picked up.
4. Open **Admin → Modules**, locate **Huntress**, and toggle it on. The
   module page shows whether each environment variable is detected without
   ever displaying the value.

The module has no other UI configuration — credentials never live in the
database.

### Managed SAT OAuth2 setup

The Managed SAT learner endpoint, `GET /accounts/{account_id}/learners`, does
not accept the HTTP Basic authentication used by the other Huntress endpoints.
It uses the OAuth2 **client credentials** grant. Create a parent/channel-partner
Managed SAT API application that can access the managed child accounts, then
add its client ID and client secret to `.env`:

```
CURRICULA_API_KEY=<OAuth2 client ID>
CURRICULA_API_SECRET=<OAuth2 client secret>
CURRICULA_BASE_URL=https://dev.curricula.com/api/v1
```

If Huntress supplies a tenant-specific API URL, use that as
`CURRICULA_BASE_URL`. MyPortal derives both OAuth2 endpoints from that URL by
removing the trailing `/api/v1`: `CURRICULA_AUTH_URL` is
`<base>/oauth/authorize`, and `CURRICULA_TOKEN_URL` is `<base>/oauth/token`.
The authorization endpoint is available for applications using the
Authorization Code flow; MyPortal's unattended SAT synchronisation uses the
token endpoint directly with the Client Credentials flow. At sync time
MyPortal posts `grant_type=client_credentials` and the required read scopes
(`account:read`, `assignments:read`,
`assignments:learner-activity`, and `learners:read`) using the client ID and
secret as HTTP Basic credentials. It then sends the returned access token as
`Authorization: Bearer ...` on every Curricula API request. Tokens and secrets
are never stored in the database or written to logs.

In each company's edit page, set **Huntress SAT account ID** to the child
account ID used in the learner URL. This is distinct from the Huntress
organisation ID used by EDR and other product endpoints.

## Linking companies to Huntress organisations

Huntress organises data by *organisation*. To link a MyPortal company to a
Huntress organisation:

1. Go to **Admin → Companies → Edit** for the company.
2. Set **Huntress organisation ID** to the organisation's ID from the
   Huntress portal.
3. Save.

The nightly **Refresh company external IDs** job also performs an
exact-name match against `GET /organizations` and populates the field
automatically when it finds a match.

## Daily sync

A global scheduler job, `huntress-daily-sync`, runs at 04:00 store-local
time. It iterates every company that has a Huntress organisation ID, calls
each product endpoint, and writes the results to the
`huntress_edr_stats`, `huntress_itdr_stats`, `huntress_sat_stats`,
`huntress_sat_learner_assignments`, `huntress_siem_stats`, and
`huntress_soc_stats` tables.

Reports always read from these snapshot tables — no live API calls are
made when rendering a report. The snapshot timestamp is shown in each
section's header so admins can see when the data was last refreshed.

Admins can also run the sync ad-hoc from the **Scheduled Tasks** UI by
adding a task with command **Sync Huntress data**. Setting a company on
the task scopes the run to that single company.

## Report sections

Five new sections are available in the per-company report settings page:

| Section | Summary | Detailed view |
| --- | --- | --- |
| Huntress EDR | Active incidents, resolved incidents, signals investigated | Same counters with snapshot timestamp |
| Huntress ITDR | Identity Threat signals investigated | Same number with snapshot timestamp |
| Huntress Security Awareness Training | Avg completion %, avg score, phishing clicks/compromises/reports | Per-learner per-assignment table with click / compromise / report rates |
| Huntress Managed SIEM | Total bytes collected (last 30 days) rendered in GB | Same value plus the window date range |
| Huntress SOC | Total events analysed by the SOC | Same value with snapshot timestamp |

Each section is hidden automatically when there is no snapshot data for
the company yet, matching the existing auto-hide-empty behaviour.

## Rate limiting and resilience

* Huntress product requests use HTTP Basic auth (`api_key:api_secret`), while
  Managed SAT learner requests exchange their credentials for an OAuth2 bearer
  token. All HTTP clients use a 30 s timeout per request.
* A short sleep is enforced between calls to stay well under Huntress's
  documented 60 req/min limit.
* If one product endpoint errors, the rest of the snapshot still updates —
  failures are logged with the redacted URL but never raised into the
  scheduler.
