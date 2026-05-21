# Staff API polling for third-party integrations

This guide documents company-scoped polling against `GET /api/staff`.

## Endpoint

`GET /api/staff?companyId={company_id}`

Company-scoped requests support additional filters for incremental syncing:

- `onboardingComplete` (`true` / `false`)
- `onboardingStatus` (exact match, case-insensitive)
- `createdAfter` (ISO-8601 timestamp; returns records with `created_at` strictly greater than this value)
- `updatedAfter` (ISO-8601 timestamp; returns records with `updated_at` strictly greater than this value)
- `cursor` (`{updated_at_iso}|{id}`)
- `pageSize` (1-500, default 200)

## Ordering guarantees

Results are ordered deterministically by:

1. `updated_at` ascending
2. `id` ascending

This ordering is stable and intended for idempotent polling loops.

## Cursor semantics

Cursor-based windows use a strict "greater than" boundary:

- `updated_at > cursor.updated_at`
- OR `updated_at == cursor.updated_at AND id > cursor.id`

Build the next cursor from the final item in the current page:

`{last.updated_at}|{last.id}`

## Idempotent polling strategy

1. Start with no cursor and choose a `pageSize`.
2. Process results in order.
3. Persist the cursor from the final processed record.
4. On the next run, request with that cursor.
5. If a run fails mid-page, restart with the previous saved cursor (already-processed rows can be safely deduplicated by `id` + `updated_at`).

## Onboarding lifecycle fields

Staff payloads now include:

- `onboarding_status`
- `onboarding_complete`
- `onboarding_completed_at`
- `created_at`
- `updated_at`

Create flows initialize onboarding state to:

- `onboarding_status = "requested"`
- `onboarding_complete = false`
- `onboarding_completed_at = null`
