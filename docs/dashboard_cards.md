# Dashboard Cards

The portal dashboard is composed of small, self-contained "cards" that the
user can add, remove, move, and resize. Each card is defined declaratively
in `app/services/dashboard_cards.py`. The page renders cards into a 12-column
CSS grid (collapsing to 6 columns on tablet and 2 on mobile) and persists
each user's layout in the existing `user_preferences` table under the key
`dashboard:layout:v1`.

## Adding a new card

Adding a card is two small pieces of code:

1. Append a `CardDescriptor(...)` to `_CARD_REGISTRY` in
   `app/services/dashboard_cards.py`. The descriptor declares an `id`
   (stable string), `title`, `description`, `category`, `default_size`,
   `permission_check`, `data_loader`, and a `template_partial`.
2. Create the Jinja partial under
   `app/templates/partials/dashboard_cards/`. The template receives the
   payload returned by `data_loader` as the `payload` variable.

Existing partials cover most patterns:

- `counter.html` — single big number with optional label/description/link.
- `status_list.html` — labelled rows with a value column.
- `entry_list.html` — list of `{title, subtitle}` rows.
- `invoice_health.html`, `staff_summary.html`, `quick_actions.html`,
  `agent.html` — domain-specific shapes.

Re-use one of these where possible before introducing a new partial.

## Permission model

Permissions are enforced on the **server**: every layout read and every
card data load runs the descriptor's `permission_check`. If the check
fails the card is dropped from both the catalogue and the saved layout
the next time the dashboard is rendered or the layout API is read.

`permission_check` callables receive a `CardContext` carrying the request,
user, super-admin flag, active company id, the membership row for the
active company, and a lookup of available modules. Always reuse the
existing services (`company_access`, `staff_access`, role/permission
flags, `modules.list_modules`) — never re-implement permission logic
inside a card.

## Sizing

`default_size` is one of `small`, `medium`, `wide`, `large`, `tall`, or
`full`. These map to grid spans in `SIZE_PRESETS`. Users can override the
size at any time via the resize handle in edit mode; sanitisation clamps
all coordinates and dimensions to the safe ranges in `sanitise_layout`.

## API

Five endpoints are exposed under `/api/dashboard/`:

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| GET    | `/catalogue`                  | The cards the current user is allowed to add. |
| GET    | `/layout`                     | The user's saved layout (or the synthesised default). |
| PUT    | `/layout`                     | Replace the saved layout. Server filters out unknown or disallowed cards. |
| POST   | `/layout/reset`               | Delete the saved layout (defaults will apply on next render). |
| GET    | `/cards/{id}`                 | A single card's payload, used for client refresh and add-card preview. |

Write endpoints go through the global CSRF middleware. Layout payloads
have a 16 KiB limit (inherited from `user_preferences`) and are validated
against the registry to prevent stored XSS or DoS via oversized arrays.
