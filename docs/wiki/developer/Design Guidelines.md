---
version: alpha
name: MyPortal
description: >
  Dark-mode-first customer portal UI. Deep navy backgrounds with sky-blue
  primary actions, subtle slate borders, and a glassmorphism aesthetic.
colors:
  # Background hierarchy
  bg-base:     "#0f172a"       # body gradient start — slate-900
  bg-gradient: "#1f2937"       # body gradient end  — gray-800
  bg-surface:  "#1e293b"       # cards, panels       — slate-800 @ 85% opacity
  bg-overlay:  "#0f172a"       # modals, dropdowns    — slate-900 @ 95% opacity
  bg-sidebar:  "#0f172a"       # sidebar              — slate-900 @ 85% opacity

  # Interactive
  primary:        "#38bdf8"   # sky-400 — primary buttons, links, focus rings
  primary-hover:  "#7dd3fc"   # sky-300 — link hover
  primary-shadow: "rgba(56,189,248,0.8)"  # button glow shadow

  # Semantic
  success:  "#bbf7d0"   # green-200 — text on success pill (status--active / operational)
  warning:  "#fde68a"   # amber-200 — text on warning pill (status--degraded)
  danger:   "#fecaca"   # red-200   — text on danger pill (status--error / outage)
  info:     "#bfdbfe"   # blue-200  — text on info pill (status--processing)
  neutral:  "#e2e8f0"   # slate-200 — general muted text

  # Text
  text:         "#f9fafb"   # gray-50 — primary body text
  text-primary: "#f8fafc"   # slate-50
  text-muted:   "#94a3b8"   # slate-400 — secondary / helper text
  text-subtle:  "rgba(226,232,240,0.75)"  # slate-200 @ 75%

  # Borders
  border:        "rgba(148,163,184,0.12)"  # slate-400 @ 12% — card borders
  border-medium: "rgba(148,163,184,0.25)"  # slate-400 @ 25% — modal borders
  border-strong: "rgba(148,163,184,0.40)"  # slate-400 @ 40% — ghost-button borders

  # Surface layers (transparency steps)
  surface-1: "rgba(148,163,184,0.08)"   # nav items, stat chips
  surface-2: "rgba(148,163,184,0.18)"   # secondary button background
  surface-3: "rgba(148,163,184,0.28)"   # hover on secondary button

  # Status pill backgrounds (paired with matching text color above)
  success-soft:   "rgba(134,239,172,0.18)"
  warning-soft:   "rgba(251,191,36,0.18)"
  danger-soft:    "rgba(239,68,68,0.22)"
  info-soft:      "rgba(96,165,250,0.20)"
  maintenance-soft: "rgba(253,224,71,0.20)"
  partial-outage-soft: "rgba(251,146,60,0.20)"

typography:
  body:
    fontFamily: "Inter, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 1rem
    lineHeight: "1.6"
  body-sm:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 0.95rem
  body-xs:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 0.85rem
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 0.75rem
    fontWeight: "600"
    letterSpacing: "0.06em"
    # text-transform: uppercase — used for section labels, stat labels
  heading-page:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 1.1rem
    fontWeight: "600"
    # .header__title — top-of-page titles inside .layout__header
  heading-card:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 1.35rem
    fontWeight: "600"
    # .card__title — section headings inside .card--panel
  heading-modal:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 1.5rem
    fontWeight: "600"
    # .modal__title
  heading-auth:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 2rem
    fontWeight: "700"
    # .auth-card__title
  stat-value:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 1.4rem
    fontWeight: "600"
    # .stat__value — KPI counters
  mono:
    fontFamily: "'JetBrains Mono', 'Fira Code', 'SFMono-Regular', ui-monospace, monospace"
    fontSize: 0.9rem
    # code, pre, log viewers

rounded:
  sm:   "0.5rem"    # nav items, small chips
  md:   "0.75rem"   # cards, table cells, images
  lg:   "0.85rem"   # inputs, buttons, form-checkbox
  xl:   "1rem"      # modals
  xxl:  "1.25rem"   # auth card
  full: "999px"     # status pills, avatar circles, badges

spacing:
  compact:        "max(0.35rem, 5px)"   # --space-compact       (dense padding)
  compact-inline: "max(0.45rem, 5px)"   # --space-compact-inline (sidebar inner padding)
  gap-tight:      "max(0.35rem, 5px)"   # --space-gap-tight     (icon/label gaps)
  gap-base:       "max(0.5rem, 5px)"    # --space-gap-base      (between form fields, card children)
  gap-roomy:      "max(0.65rem, 7px)"   # --space-gap-roomy     (form actions, section gaps)

components:
  # ── Buttons ───────────────────────────────────────────────────────────────
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#0f172a"
    rounded: "{rounded.lg}"
    padding: "var(--space-gap-tight) calc(var(--space-gap-roomy) * 1.2)"
    # box-shadow: 0 18px 30px -18px {colors.primary-shadow}

  button-primary-hover:
    backgroundColor: "{colors.primary}"
    # transform: translateY(-1px); box-shadow elevated

  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.neutral}"
    rounded: "{rounded.lg}"
    padding: "var(--space-gap-tight) var(--space-gap-roomy)"

  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.neutral}"
    rounded: "{rounded.lg}"
    padding: "var(--space-gap-tight) var(--space-gap-roomy)"
    # border: 1px solid {colors.border-strong}

  button-danger:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger}"
    rounded: "{rounded.lg}"
    padding: "var(--space-gap-tight) var(--space-gap-roomy)"
    # border: 1px solid rgba(248,113,113,0.6)

  button-small:
    padding: "var(--space-compact) var(--space-gap-base)"
    typography: "{typography.body-xs}"

  button-compact:
    padding: "0.5rem 0.875rem"
    typography: "{typography.body-xs}"

  # ── Cards ─────────────────────────────────────────────────────────────────
  card:
    backgroundColor: "rgba(30,41,59,0.85)"
    rounded: "{rounded.md}"
    padding: "clamp(0.9rem, 2vw, 1.5rem)"
    # border: 1px solid {colors.border}
    # box-shadow: 0 16px 32px -28px rgba(15,23,42,0.8)

  card-panel:
    # extends card; flex-direction: column; gap: var(--space-gap-base)
    # Use for all main content cards in the page body

  card-collapsible:
    # <details> element; toggle icon rotates 180° when [open]
    rounded: "{rounded.md}"

  # ── Inputs ────────────────────────────────────────────────────────────────
  input:
    backgroundColor: "rgba(15,23,42,0.70)"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    padding: "var(--space-gap-tight) var(--space-gap-base)"
    # border: 1px solid rgba(148,163,184,0.3)

  input-focus:
    # border-color: rgba(56,189,248,0.8)
    # box-shadow: 0 0 0 3px rgba(56,189,248,0.25)

  # ── Status pills ──────────────────────────────────────────────────────────
  status-pill:
    rounded: "{rounded.full}"
    padding: "var(--space-compact) var(--space-gap-tight)"
    typography: "{typography.body-xs}"
    # font-weight: 600; text-transform: capitalize

  # ── Modal ─────────────────────────────────────────────────────────────────
  modal-overlay:
    backgroundColor: "rgba(15,23,42,0.75)"
    # backdrop-filter: blur(6px)

  modal-content:
    backgroundColor: "rgba(15,23,42,0.95)"
    rounded: "{rounded.xl}"
    padding: "calc(var(--space-gap-roomy) * 2.5)"
    # border: 1px solid {colors.border-medium}
    # max-width: 90vw; max-height: 90vh

  # ── Stat strip (KPI counters) ─────────────────────────────────────────────
  stat:
    backgroundColor: "{colors.surface-1}"
    rounded: "{rounded.lg}"
    padding: "0.75rem 1rem"
    # border: 1px solid {colors.border}

  # ── Table ─────────────────────────────────────────────────────────────────
  table-row-hover:
    backgroundColor: "rgba(148,163,184,0.06)"

  table-header:
    # background: rgba(15,23,42,0.6); sticky top; font-weight 600; font-size body-xs
    backgroundColor: "rgba(15,23,42,0.60)"
---

## Overview

MyPortal is a **dark-mode-first customer portal** with a glassmorphism
aesthetic. Deep slate/navy backgrounds layered with translucent surface panels,
a single sky-blue primary accent, and restrained slate borders combine to
produce a professional, low-distraction environment for IT staff and customers
alike.

The design language is intentionally minimal: colour is used only for
semantic communication (green = ok, amber = warning, red = danger, blue =
primary) and every interactive element carries a focus ring that meets WCAG AA.

## Colors

The palette is rooted in Tailwind's **Slate** and **Sky** colour ramps used at
carefully chosen opacity levels to build the layering effect.

- **`bg-base` / `bg-gradient` (#0f172a → #1f2937):** The body background is a
  `135deg` CSS gradient between these two values, giving depth without imagery.
- **`bg-surface` (rgba(30,41,59,0.85)):** All content cards sit on this
  semi-transparent dark slate. The slight transparency lets the gradient bleed
  through on supported renderers.
- **`primary` (#38bdf8):** Sky-400. The one interactive colour — used for
  primary buttons (with a glow shadow), focus rings, link text, and
  form-field focus borders. Hover uses `#7dd3fc` (sky-300) for legibility.
- **`text` / `text-primary` (#f9fafb, #f8fafc):** Near-white for all body copy.
  `text-muted` (#94a3b8, slate-400) for secondary text, labels, and hints.
- **`border` (rgba(148,163,184,0.12)):** Subtle 12%-opacity slate. Intentionally
  almost invisible — borders should separate, not decorate.
- **Semantic backgrounds:** Danger/success/warning pill backgrounds are the
  matching Tailwind colour at ~18–22% opacity. This keeps them readable without
  drawing the eye away from primary content.

### Dark/Light mode

The app currently ships a single **dark** theme. The `:root` sets
`color-scheme: light dark` as a future affordance. All custom properties are
already namespaced under `var(--color-…)` so a `[data-theme="light"]` layer
can be added without altering component markup.

## Typography

A single sans-serif family (**Inter**) is used for all UI text. Monospaced
content (code, log viewers, terminal output) uses **JetBrains Mono** / Fira Code.

| Token            | Size    | Weight | Use                                    |
|------------------|---------|--------|----------------------------------------|
| `body`           | 1 rem   | 400    | Default paragraph / table cell text    |
| `body-sm`        | 0.95rem | 400    | Subtitles, card hints                  |
| `body-xs`        | 0.85rem | 400/600| Status pills, table header labels      |
| `label`          | 0.75rem | 600    | UPPERCASE section labels, stat labels  |
| `heading-page`   | 1.1rem  | 600    | `.header__title` — top-of-page         |
| `heading-card`   | 1.35rem | 600    | `.card__title` — section headings      |
| `heading-modal`  | 1.5rem  | 600    | `.modal__title`                        |
| `heading-auth`   | 2rem    | 700    | Login / register card titles           |
| `stat-value`     | 1.4rem  | 600    | KPI counter values in stat strips      |
| `mono`           | 0.9rem  | 400    | Code, pre, log viewers                 |

**Rule:** Never size a heading inside a card to `h1`/`2rem`. Page-level identity
lives in the top header; the first card on a page must not repeat the page title.

## Layout

Every authenticated page follows a strict three-zone shell:

```
┌──────────────────────────────────────────────────────────────────┐
│  layout__sidebar (240–280 px, fixed on tablet)                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  layout__header (sticky, 100% width)                     │    │
│  │  [title · meta]                        [Actions ▾]       │    │
│  ├──────────────────────────────────────────────────────────┤    │
│  │  layout__content (scrollable)                            │    │
│  │  [stat-strip (optional)]                                 │    │
│  │  [card--panel]  [card--panel]  …                         │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

- **Sidebar** (`layout__sidebar`): 240–280 px on desktop, slides off-canvas on
  tablet (≤1024 px). Always uses `bg-sidebar` with `backdrop-filter: blur(12px)`.
- **Page header** (`layout__header`): Sticky top-of-page bar. Contains title on
  the left, page-level actions on the right (rendered via the
  `page_header_actions` Jinja macro from `templates/macros/header.html`).
  List pages also put their list controls here, and record pages put the
  record name and status here (see **Page header bar** below).
- **Content area** (`layout__content`): Vertically scrollable. Starts with an
  optional KPI stat strip, then one or more `.card.card--panel` sections.

### Page header bar

The header bar is the page's toolbar, not just its title. Anything that
controls the whole page goes up there so the content area can give its full
width to the stat strip and the main table or cards.

**Reference implementations:** the ticket list (`app/templates/admin/tickets.html`)
and the admin ticket detail page (`app/templates/admin/ticket_detail.html`).

**List pages** put every list control in `{% block header_title %}` inside a
`.header-title-menu`, in this order:

```
[Title] [Saved view ▾] [Save] [Update] [Delete]  [Quick search] [Limit ▾]
        [Group by ▾] [Columns ▾] [Stats ▾]  Showing X of Y     [New ▾] [Tools ▾]
```

Every other list page uses the same pattern with the generic classes below,
so the ticket-specific `.ticket-header-toolbar` is only needed on the ticket
list. A typical page:

```html
{% block header_title %}
  <div class="page-header-bar">
    <span class="header__title-text">Companies</span>
    <div class="page-header-toolbar">
      <label class="visually-hidden" for="companies-filter">Filter companies</label>
      <input id="companies-filter" type="search"
             class="form-input page-header-toolbar__search"
             placeholder="Filter companies" data-table-filter="companies-table" />
      <label class="page-header-toolbar__check">
        <input type="checkbox" … /> <span>Show archived</span>
      </label>
      {{ table_column_picker("companies", companies_columns) }}
    </div>
  </div>
{% endblock %}
```

| Class / macro | Use |
|---|---|
| `.page-header-bar` | Wrapper in `header_title`: title, then toolbar, then (optionally) a tools dropdown pushed to the right. |
| `.page-header-toolbar` | The control row. Can be the `<form method="get">` itself; use `.page-header-toolbar__group` for a nested form or a cluster of controls. |
| `__search`, `__select`, `__number` | Compact widths for search inputs, selects (including searchable selects) and small number inputs. |
| `__check` | A muted inline checkbox label (Show archived, Show inactive). |
| `__info` | Muted result count or summary (`3 services`, `Showing X of Y`). |
| `page_header_filter_menu` (`macros/header.html`) | "Filters ▾" panel for pages with more fields than fit in the bar (audit logs). Keep search and the one or two most used filters in the bar; the rest go in the panel. |
| `table_toolbar(…, header=true)` (`macros/tables.html`) | The standard table toolbar rendered for the header bar. |

- Keep the title short (one or two words). The toolbar wraps onto a second
  line before the header actions do; at 768 px and below it takes the full
  width under the title.
- Inputs and selects carry a `.visually-hidden` `<label>`, and use the
  placeholder or first option for the visible hint. Put any longer hint in a
  `title` attribute. Buttons use `button--compact`.
- Selects in a GET filter form submit on change, so an "Apply" button is only
  needed for free-text fields (or as a `<noscript>` fallback).
- The "Showing X of Y" count is a muted text span with `data-table-info` and
  `aria-live="polite"`, not a row above the table.
- The content area then starts with a full-width `counter_strip`, followed
  by the table. Don't put filter panels or sidebars beside the stat strip.
- Scripts that drive these controls look them up with
  `document.querySelector`, not inside the content container, because the
  header renders outside it.
- Pages built around several peer tables (IPAM, Defender) or around a form
  or workspace (documentation search, rack designer) keep their per-section
  controls in the content.

**Record pages** (one ticket, company, asset…) make the record itself the
header:

```
[Record name]                          [Back to list] [External links] [Actions ▾]
[status pill] muted meta · meta
```

- The record name replaces the generic page title (`Ticket detail` becomes
  the ticket subject). Its status pill sits under it with no "SLA:" or
  "Status:" prefix. Supporting meta (paused timers, due dates) follows as
  muted text, with dates in `<span data-utc>`.
- Don't repeat the name, status or navigation in a second header inside the
  content area.
- "Back to …" and links to external systems (Hudu, Solidtime) are ghost
  buttons directly left of the Actions menu.
- Everything else goes in the Actions menu, rendered with
  `page_header_overflow` from `templates/macros/header.html`. Destructive
  items (Delete) and one-off operations (Bill Now) live there, last, with
  `variant: "danger"` for destructive ones and a `window.confirm` (or
  `data-confirm`) that says what will happen. Don't give them their own card.
- Below 1100 px the header actions may wrap; the record name truncates with
  an ellipsis and keeps a `title` attribute with the full text.
- Outside the ticket page, wrap the name and meta in
  `<div class="page-header-bar page-header-bar--record">` with the meta in
  `.page-header-bar__meta`. When a record page also has filters (SMB1001
  controls), nest that block inside a `.page-header-bar` before the
  `.page-header-toolbar`.

### Responsive breakpoints

| Name    | Breakpoint   | Behaviour                              |
|---------|-------------|----------------------------------------|
| mobile  | ≤ 640 px    | Single-column, sidebar hidden          |
| tablet  | ≤ 1024 px   | Sidebar off-canvas (hamburger toggle)  |
| desktop | ≥ 1025 px   | Full two-column layout                 |

Utility classes: `.u-hide-mobile`, `.u-only-mobile`, `.u-hide-tablet`.

### Spacing scale

All spacing in components uses the five CSS custom properties defined in `:root`:

| Token               | Value              | Typical use                         |
|---------------------|--------------------|-------------------------------------|
| `--space-compact`   | max(0.35rem, 5px)  | Inner padding of dense elements     |
| `--space-compact-inline` | max(0.45rem, 5px) | Sidebar horizontal padding      |
| `--space-gap-tight` | max(0.35rem, 5px)  | Icon-to-label gaps, badge spacing   |
| `--space-gap-base`  | max(0.5rem, 5px)   | Between form fields, card children  |
| `--space-gap-roomy` | max(0.65rem, 7px)  | Form-action rows, section spacing   |

**Rule:** Never hard-code spacing values in new components. Use these variables
or multiples of them (`calc(var(--space-gap-roomy) * 2.5)`).

## Elevation & Depth

Elevation is expressed through **background opacity**, **box-shadow blur**, and
**backdrop-filter** — not through solid fills.

| Layer       | Background                     | Shadow                                  |
|-------------|--------------------------------|-----------------------------------------|
| Body        | gradient (#0f172a → #1f2937)   | —                                       |
| Sidebar     | rgba(15,23,42,0.85) + blur(12) | inset -1px 0 rgba(255,255,255,0.05)     |
| Card        | rgba(30,41,59,0.85)            | 0 16px 32px -28px rgba(15,23,42,0.80)   |
| Header bar  | rgba(15,23,42,0.70) + blur(10) | border-bottom 1px rgba(148,163,184,0.12)|
| Modal overlay | rgba(15,23,42,0.75) + blur(6) | —                                      |
| Modal panel | rgba(15,23,42,0.95)            | 0 24px 50px -20px rgba(15,23,42,0.85)   |
| Primary btn | #38bdf8                        | 0 18px 30px -18px rgba(56,189,248,0.80) |

**Rule:** When adding a new floating element (dropdown, tooltip, popover), pick
the next-higher opacity step from the table above. Do not introduce new
`box-shadow` values without referencing this hierarchy.

## Shapes

All radii are chosen from a geometric progression:

| Token    | Value   | Use                                                |
|----------|---------|----------------------------------------------------|
| `sm`     | 0.5rem  | Nav items, small inner chips, toggle buttons       |
| `md`     | 0.75rem | Cards, table rows, image thumbnails, brand logo    |
| `lg`     | 0.85rem | Inputs, textarea, form-checkbox, primary buttons   |
| `xl`     | 1rem    | Modal panels                                       |
| `xxl`    | 1.25rem | Auth card (login / register)                       |
| `full`   | 999px   | Status pills, avatar circles, numeric badges       |

## Components

### Buttons

Four semantic variants plus two size modifiers:

| Class               | Purpose                                    |
|---------------------|--------------------------------------------|
| `.button`           | Primary — sky-400 fill with glow shadow    |
| `.button--secondary`| Subdued slate fill, no glow                |
| `.button--ghost`    | Transparent + slate border                 |
| `.button--danger`   | Red-tinted, red border — destructive actions|
| `.button--small`    | Size modifier — 0.85rem / compact padding  |
| `.button--compact`  | Size modifier — 0.875rem / 0.5rem padding  |
| `.button--icon`     | Square icon-only button (2.25 rem min)     |
| `.button--processing` | Disabled + spinner state during submit   |
| `.button-link`      | Inline hyperlink-style button (no fill)    |

**Rule:** All page-level actions live in the `page_header_actions` macro. Never
place `.button.button` inside a `.card__body` as the primary page action.

### Cards

```html
<div class="card card--panel">
  <div class="card__header">
    <h2 class="card__title">Section Title</h2>
  </div>
  <!-- content -->
</div>
```

- `.card` sets the background, border, radius, and shadow.
- `.card--panel` adds `flex-direction: column; gap: var(--space-gap-base)`.
- `.card--full` spans the full grid width (`grid-column: 1 / -1`).
- `.card-collapsible` wraps a `<details>` element for expand/collapse sections.

### Status Pills

```html
<span class="status status--success">Active</span>
```

Pill variants: `success`, `warning`, `danger`, `info`, `neutral`,
`active`, `invited`, `suspended`, `processing`, `error`,
`operational`, `maintenance`, `degraded`, `partial_outage`, `outage`.

All pills: `border-radius: 999px`, `font-size: 0.85rem`, `font-weight: 600`,
`text-transform: capitalize`, using the `*-soft` background tokens.

### Stat Strip (KPI counters)

Use the `counter_strip` Jinja macro from `templates/macros/counters.html`:

```jinja
{% from "macros/counters.html" import counter_strip %}
{{ counter_strip(items, total=total_count, total_label="Tickets") }}
```

Each item: `{"label": str, "value": int|str, "variant": str}`. Variants mirror
the status-pill names. Emits `.stat-strip` / `.stat` markup.

### Tables

Every data table follows the shape below. On a page whose main content is
that table, the toolbar lives in the header bar (see **Page header bar**):

```
[ search ] [ filter ] [ filter ] … [ Columns ▾ ] [ Bulk actions ▾ ]
```

Use macros from `templates/macros/tables.html`: `data_table`, `table_toolbar`,
`table_column_picker`, `empty_state`. Column visibility is persisted via
`app/static/js/table_columns.js` with `localStorage` + server preferences API.

Timestamps inside tables must use `<span data-utc="ISO-string">…</span>` so
`main.js` localises them to the browser timezone. Never call `strftime` in
templates for user-facing dates.

### Forms

Use macros from `templates/macros/forms.html` (`form_field`, `form_actions`)
for all create/edit screens. Prefer grouped form inputs over raw JSON textareas.

Layout helper: `.form-grid` creates a responsive `auto-fit minmax(220px, 1fr)`
column grid for multi-field screens.

### Modals

**Reference implementation:** the *Staff custom fields* editor
(`#staff-custom-field-modal` in `app/templates/admin/company_edit.html`, driven
by `app/static/js/staff_custom_fields_admin.js`). The Add staff member editor in
`app/templates/staff/index.html` follows the same pattern.

Every popup modal is a `<div>`, not a `<dialog>`. The shared handler in
`app/static/js/main.js` closes open modals on Escape, backdrop click and any
`[data-modal-close]` control by setting `hidden`. `docs/design_audit_scan.py`
flags `<dialog class="modal">` as non-conforming, so convert any existing
`<dialog>` modals when you touch the page.

The rack management page (`app/templates/infrastructure/racks.html`) still
uses an older native `<dialog class="dialog">` component. Follow its layout
principles, but build new modals with the `.modal` pattern below.

```html
<div
  class="modal"
  id="thing-modal"
  role="dialog"
  aria-modal="true"
  aria-labelledby="thing-modal-title"
  hidden
>
  <form class="modal__panel" method="post" action="/route" novalidate>
    {% include "partials/csrf.html" %}

    <header class="modal__header">
      <div>
        <h2 class="modal__title" id="thing-modal-title">Add thing</h2>
        <p class="text-muted">One sentence saying what saving will do.</p>
      </div>
      <button type="button" class="modal__close" data-modal-close aria-label="Close thing editor"></button>
    </header>

    <div class="modal__body">
      <div class="form-field">
        <label class="form-label" for="thing-name">Name</label>
        <input id="thing-name" name="name" class="form-input" />
      </div>
    </div>

    <footer class="modal__footer">
      <button type="button" class="button button--ghost" data-modal-close>Cancel</button>
      <button type="submit" class="button button--primary">Save thing</button>
    </footer>
  </form>
</div>
```

Add `.modal__panel--wide` for multi-section editors. A short confirmation or
read-only modal with no form may use a single `.modal__content` wrapper in
place of the panel, keeping the same outer attributes, close button and title.

The trigger carries a `data-<name>-modal-open` attribute and points at the
modal with `aria-controls` and `aria-haspopup="dialog"`.

Rules:

- The outer element is a `<div class="modal">` with `role="dialog"`,
  `aria-modal="true"`, `aria-labelledby` and the `hidden` attribute. Never
  hide a modal with `style="display:none"`.
- `hidden` already removes a closed modal from the accessibility tree, so
  `aria-hidden` is not required. If a page sets `aria-hidden="true"`, its JS
  must switch it to `false` on open, or screen readers will skip the modal.
- The `id` of the `.modal__title` heading matches `aria-labelledby`.
- The `.modal__close` button carries `data-modal-close` and an `aria-label`
  naming what it closes.
- Forms in modals include `{% include "partials/csrf.html" %}`. Put fields
  in `.modal__body` and actions in `.modal__footer`: ghost Cancel, then the
  primary submit. Destructive confirmations use `button--danger`.
- Use `.form-grid` for pairs of related fields (priority and status, start and
  end date).
- Never nest a modal inside a host page `<form>`; the modal's form is
  self-contained.

## Do's and Don'ts

### Do

- ✅ Use `var(--color-…)` and `var(--space-…)` tokens; never hard-code hex or px
  values in new CSS rules.
- ✅ Put page-level actions in the `page_header_actions` macro (top-right header).
- ✅ Put a list page's saved views, search, limit, grouping, columns, stats
  picker and result count in the header bar so the stat strip and table get
  the full width.
- ✅ On record pages, show the record name and status pill in the header bar,
  with Back and external links directly left of the Actions menu.
- ✅ Use `.card.card--panel` as the content container; let the sidebar and header
  handle navigation and identity.
- ✅ Use `<span data-utc="…">` for all displayed timestamps.
- ✅ Render status with `.status.status--<variant>` pills.
- ✅ Use the `data_table` / `table_toolbar` macros for every data table.
- ✅ Use the `<div class="modal" role="dialog" … hidden>` pattern for all
  overlay dialogs (see Modals above).
- ✅ Ensure every interactive element has a visible focus style (the default
  ring uses `rgba(148,163,184,0.35)` — keep it or strengthen it).

### Don't

- ❌ Don't repeat the page title inside the first card — it already lives in the
  header.
- ❌ Don't give destructive or one-off record operations (Delete, Bill Now)
  their own card or button in the content area; put them in the Actions menu
  with a confirmation.
- ❌ Don't place filter or saved-view panels beside the stat strip; they belong
  in the header bar.
- ❌ Don't use hard-coded colours (`#38bdf8`, `rgba(…)`) in new component CSS;
  reference the token instead.
- ❌ Don't add a new `box-shadow` depth value that isn't in the Elevation table.
- ❌ Don't add per-table column-persistence scripts; use the generic
  `table_columns.js` with `data-table-id`.
- ❌ Don't use `strftime` / Python date formatting in Jinja templates for dates
  shown to users.
- ❌ Don't place a `.button` (primary style) inside a `.card__body` as the sole
  CTA for a page — all page-level CTAs belong in the header.
- ❌ Don't exceed the viewport width; all layouts use `max-width: 100vw` and
  `overflow-x: hidden`.
