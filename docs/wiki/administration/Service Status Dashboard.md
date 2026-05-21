# Service status dashboard

## Overview
The service status feature gives every authenticated user access to `/service-status`, which renders a company-aware dashboard summarising each tracked service, its status badge, and the most recent update timestamp. Super admins also see a shortcut to `/admin/service-status`, where they can edit services and adjust customer visibility assignments. Both pages reuse the status metadata defined in the service layer, so the colour, label, and description stay consistent across the UI. 

## Database schema
The `migrations/139_service_status_dashboard.sql` migration introduces two tables:
- `service_status_services` stores the service name, description, status, optional message, ordering, activity flag, timestamps, and `updated_by` staff ID. It also indexes the status/order columns to speed up filtering and defaults new rows to the `operational` status.
- `service_status_service_companies` is the mapping table between services and companies, enabling per-customer visibility control and cascading deletes.

Run the migration whenever you deploy to a new environment so the admin UI and APIs can read/write service status data.

## Service layer
`app/services/service_status.py` holds the shared logic:
- `STATUS_DEFINITIONS` lists the allowed states (`operational`, `maintenance`, `degraded`, `partial_outage`, `outage`) along with the badge label, description, and CSS variant class used by the templates.
- CRUD helpers (for example `create_service`, `update_service`, and `delete_service`) sanitise payloads, normalise statuses, and fan out to the repository layer.
- `list_services_for_company` filters the global list down to the active company (or leaves services public when no company is specified).
- `summarise_services` aggregates per-status counts for the dashboard stats tiles.

Because every UI/API entry point flows through this module, you get the same validation behaviour no matter where updates originate.

## Admin UI
The `/admin/service-status` page requires super-admin rights. It renders a searchable table of services with inline visibility badges, status chips, and edit/delete actions, plus a stacked form for adding or editing a tile. Company assignments are handled via a multi-select, the order is controlled through a numeric input, and an “Active” toggle lets admins hide entries without deleting them. The form posts to `POST /admin/service-status` for creation and `POST /admin/service-status/{id}` for edits, with `_extract_service_status_form` parsing the body.

## Customer-facing dashboard
The `/service-status` page uses the same summary data to display the aggregate status counts and a responsive card grid for each service. When a service includes a `status_message`, the card surfaces it, otherwise the template falls back to the default description of the selected status. If the logged-in user’s active company lacks any assigned services, the page shows an empty-state message instead of cards.

## API surface
`app/api/routes/service_status.py` exposes a `/api/service-status/services` collection for CRUD operations and a targeted status update endpoint:
- `GET /services` resolves the caller’s active company context (or accepts `company_id`) and returns the filtered list. Super admins can optionally include inactive rows.
- `POST /services`, `PUT /services/{id}`, and `DELETE /services/{id}` require super-admin credentials and allow full lifecycle management including company assignments.
- `PATCH /services/{id}/status` accepts either a super-admin session or an API key. API-key holders must still meet any route/ip restrictions configured in `get_optional_api_key`. Provide a simple payload containing `status` and, optionally, `status_message` to let external monitors update the dashboard.

All responses use the Pydantic models from `app/schemas/service_status.py`, keeping the JSON contract consistent between UI forms and automation scripts.

## Permissions recap
- All authenticated users can open `/service-status`.
- Only super admins can reach `/admin/service-status` or call the CRUD endpoints.
- The status-only API accepts super admins **or** API keys, so you can wire health checks, monitoring tools, or other third parties into the dashboard without giving them broad admin sessions.

## Operational checklist
1. Apply migration 139 in every environment.
2. Grant API keys route access to `PATCH /api/service-status/services/{id}/status` if you expect third parties to update statuses.
3. Seed at least one service through the admin UI or API so that the dashboard renders meaningful tiles.
