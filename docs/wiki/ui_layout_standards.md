# MyPortal UI Layout Standards

This document defines the standard page layout used across MyPortal. New pages
should follow these conventions, and existing pages will be migrated to them
incrementally (see `changes/` for the rollout history).

The goal is a consistent, predictable shell around every screen so users can
find primary actions, counters, search/filter controls, and table customisation
in the same place on every page.

## 1. Page anatomy

Every authenticated page is composed of:

1. **Sidebar** (left) — global navigation (`base.html` `nav.layout__sidebar`).
2. **Page header** (top) — page title, optional meta line, and a single
   right-aligned actions area (primary button + overflow "Actions ▾" menu).
3. **Optional counter strip** — KPI tiles directly under the header,
   visually identical to the Service Status dashboard.
4. **Content cards** — one or more `.card.card--panel` sections containing the
   actual data, forms, or tables.

```
┌──────────┬──────────────────────────────────────────────┐
│          │  Page title · meta              [Primary ▾] │
│ sidebar  ├──────────────────────────────────────────────┤
│          │  ┌─stat──┐ ┌─stat──┐ ┌─stat──┐ ┌─stat──┐    │
│          │  │ Total │ │ Open  │ │ Pend. │ │ Closed│    │
│          │  └───────┘ └───────┘ └───────┘ └───────┘    │
│          │  ┌──────────────────────────────────────┐   │
│          │  │ Section title                        │   │
│          │  │ [search] [filter] [Columns ▾] …      │   │
│          │  │ ─────────────────────────────────── │   │
│          │  │ table                                │   │
│          │  └──────────────────────────────────────┘   │
└──────────┴──────────────────────────────────────────────┘
```

## 2. Page title and meta

- The page title comes **only** from the `header_title` block of `base.html`.
- The first card on the page **must not** repeat the page title or its
  one-line tagline. (A card titled "Knowledge base articles" with subtitle
  "Browse curated documentation with scoped visibility." on the Knowledge Base
  page is the anti-pattern.)
- `card__header` / `card__title` are reserved for **section titles** within a
  page that has multiple sections.
- Use `header__title-meta` for short context (active company name, scope, etc.)
  next to the title.

## 3. Page actions

- **All page-level actions live in the top-right header area**, never as
  buttons inside a card body.
- Use the `page_header_actions` macro from `templates/macros/header.html`:

  ```jinja
  {% from "macros/header.html" import page_header_actions %}
  {% block header_actions %}
    {{ page_header_actions([
      {"label": "New ticket", "type": "button", "variant": "primary",
       "attrs": {"data-create-ticket-modal-open": "", "aria-haspopup": "dialog"}},
      {"label": "Configure notifications", "type": "link", "href": "/notifications/settings"},
      {"label": "Mark selected as read", "type": "button",
       "attrs": {"data-notification-mark-selected": "", "disabled": ""}},
      {"label": "Delete selected", "type": "button", "variant": "danger",
       "attrs": {"data-notification-delete-selected": "", "disabled": ""}},
    ]) }}
  {% endblock %}
  ```

  The macro renders the first `variant: "primary"` action as a button, and
  collapses the remaining actions into an overflow "Actions ▾" disclosure.

- Action types: `link`, `button`, `form` (POST with auto-included CSRF token).
- Bulk-selection actions stay in the menu but are rendered with `disabled=""`
  initially; existing per-page JS toggles them as rows are selected.

## 4. Counter strips

- Counters at the top of a page (KPIs, status breakdowns) use the
  `counter_strip` macro from `templates/macros/counters.html`:

  ```jinja
  {% from "macros/counters.html" import counter_strip %}
  {{ counter_strip(items, total=total_count, total_label="Tracked services") }}
  ```

- `items` is a list of `{"label": str, "value": int|str, "variant": str}`.
- Variants follow the Service Status palette: `total`, `operational`,
  `maintenance`, `degraded`, `partial_outage`, `outage`, plus the generic
  `success`, `warning`, `danger`, `info`, `neutral`.
- The macro emits `.stat-strip` / `.stat` markup. The legacy
  `.service-status__stats` / `.service-status__stat` class names are kept as
  CSS aliases for backwards compatibility.

## 5. Tables

Every data table on the platform follows a single shape:

```
[ search ] [ filter ] [ filter ] … [ Columns ▾ ] [ Bulk actions ▾ ]
```

Use the macros from `templates/macros/tables.html`:

- `data_table(headers, table_id="…", columns=[…])` — sortable, sticky-header
  table; emits `data-column-key` on every `<th>`.
- `table_toolbar(table_id, search=True, filters=[…], page_sizes=[…], bulk_actions=[…])`
  — renders the standard toolbar row.
- `table_column_picker(table_id, columns)` — "Columns ▾" disclosure with a
  checkbox per column.

### 5.1 Column-visibility persistence

`app/static/js/table_columns.js` is wired into `base.html`. For every
`<table data-table data-table-id="…">` it:

- Reads the saved visibility set from `localStorage` under
  `myportal:tables:<table_id>:columns`.
- For authenticated users, fetches `/api/users/me/preferences?key=tables:<table_id>:columns`
  on load and pushes back to `PUT /api/users/me/preferences` on change so
  preferences follow the user across devices. The localStorage cache is the
  fast path; the API is the source of truth.
- Toggles the `is-hidden` utility class on matching `<th>` and `<td>` cells
  (matched by `data-column-key`).
- Provides a "Reset to defaults" item in every column picker.

### 5.2 Column metadata

Each column in the `columns` list passed to `data_table` / `table_column_picker`:

| key                | required | meaning                                             |
| ------------------ | -------- | --------------------------------------------------- |
| `key`              | ✓        | machine name, used in `data-column-key` and storage |
| `label`            | ✓        | header text                                         |
| `sortable`         |          | bool, default `true`                                |
| `sort_type`        |          | `string` \| `number` \| `date`, default `string`    |
| `default_visible`  |          | bool, default `true`                                |
| `mobile_priority`  |          | `essential` \| `high` \| `medium` \| `low`          |
| `align`            |          | `left` \| `center` \| `right`                       |
| `width`            |          | CSS width override                                  |

## 6. Status pills, empty states, dates

- Status pills use `<span class="status status--<variant>">…</span>`. Variants:
  `success`, `warning`, `danger`, `info`, `neutral`. `.tag` and `.badge` are
  legacy aliases.
- Empty states use the `empty_state` macro from `templates/macros/tables.html`,
  not ad-hoc `<p class="text-muted">No …</p>`.
- Timestamps render with `<span data-utc="…">…</span>` so the existing JS in
  `main.js` localises them. **Never** use `strftime` in templates for
  user-facing dates.

## 7. Forms

- Use the macros in `templates/macros/forms.html` (`form_field`, `form_actions`)
  for all create/edit screens.
- Prefer grouped form fields over JSON textareas. Where a JSON editor is still
  in use (some automation/webhook screens), it should be tracked for migration.

## 8. Mobile

- Pages must not exceed the viewport width. Tables use the responsive
  collapsing logic in `tables.js`; columns are collapsed in
  `mobile_priority` order (lowest first).
- The column-picker selection takes precedence over the responsive collapse:
  if a user explicitly hides a column, it stays hidden on all viewports.

## 9. Theming

- All new components must use the existing CSS custom properties
  (`var(--color-…)`, `var(--space-…)`). No hard-coded colors.
- Custom logos, favicons, and theme variables continue to flow through
  `site_settings` and remain effective for the new components.

## 10. Backwards compatibility

The migration is incremental. During Phase 4 the following deprecation shims
keep unmigrated pages working:

- The legacy `service-status__stat*` CSS classes are preserved as aliases of
  the new `.stat-strip` / `.stat` rules.
- The `data_table` macro keeps its previous signature; `table_id` and
  `columns` are optional parameters that opt in to the new behaviour.
- Existing per-table column scripts (`ticket_columns.js`,
  `staff_columns.js`, etc.) continue to work; new tables should use the
  generic `table_columns.js` instead.
