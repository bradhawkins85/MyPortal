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

* The HTTP client uses HTTP Basic auth (`api_key:api_secret`) and a 30 s
  timeout per request.
* A short sleep is enforced between calls to stay well under Huntress's
  documented 60 req/min limit.
* If one product endpoint errors, the rest of the snapshot still updates —
  failures are logged with the redacted URL but never raised into the
  scheduler.
