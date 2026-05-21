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
- **Content area** (`layout__content`): Vertically scrollable. Starts with an
  optional KPI stat strip, then one or more `.card.card--panel` sections.

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

Every data table follows the shape:

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

```html
<dialog class="modal" id="my-modal">
  <div class="modal__content">
    <button class="modal__close" aria-label="Close">×</button>
    <h2 class="modal__title">Title</h2>
    <div class="modal__body">
      <!-- form or content -->
    </div>
  </div>
</dialog>
```

Prefer `<dialog>` over `<div role="dialog">`. The overlay and panel are styled
separately — the `<dialog>` itself provides the backdrop via `.modal` rules.

## Do's and Don'ts

### Do

- ✅ Use `var(--color-…)` and `var(--space-…)` tokens; never hard-code hex or px
  values in new CSS rules.
- ✅ Put page-level actions in the `page_header_actions` macro (top-right header).
- ✅ Use `.card.card--panel` as the content container; let the sidebar and header
  handle navigation and identity.
- ✅ Use `<span data-utc="…">` for all displayed timestamps.
- ✅ Render status with `.status.status--<variant>` pills.
- ✅ Use the `data_table` / `table_toolbar` macros for every data table.
- ✅ Use `<dialog class="modal">` for all overlay dialogs.
- ✅ Ensure every interactive element has a visible focus style (the default
  ring uses `rgba(148,163,184,0.35)` — keep it or strengthen it).

### Don't

- ❌ Don't repeat the page title inside the first card — it already lives in the
  header.
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
