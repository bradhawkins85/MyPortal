# MyPortal Design Gold Standards

This document captures the authoritative "gold standard" patterns for three
recurring UI components across MyPortal.  All new pages and components must
follow these patterns.  See [`docs/ui_layout_standards.md`](ui_layout_standards.md)
for the broader page-level layout rules that sit around these components.

---

## 1. Actions menu

**Reference implementation:** `/admin/tickets` (`app/templates/admin/tickets.html`)

The header actions area always consists of **one primary button** (the most
important positive action on the page) followed by a compact **dropdown menu**
for secondary and destructive actions.

### Structure

```html
<div class="header-title-menu">
  <span class="header-title-menu__label">Page title</span>

  <!-- Primary action — always a .button--primary -->
  <button
    type="button"
    class="button button--primary"
    data-<action>-open
    aria-haspopup="dialog"
    aria-controls="<modal-id>"
  >
    New item
  </button>

  <!-- Secondary / overflow actions — rendered with <details> -->
  <details class="header-title-menu__dropdown" data-header-menu>
    <summary class="header-title-menu__toggle" data-header-menu-toggle aria-haspopup="true">
      Item tools
      <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" class="header-title-menu__icon">
        <path d="M6.2 8.2a1 1 0 0 1 1.4 0L12 12.6l4.4-4.4a1 1 0 0 1 1.4 1.4l-5.1 5.1a1 1 0 0 1-1.4 0L6.2 9.6a1 1 0 0 1 0-1.4z" />
      </svg>
      <span class="visually-hidden">Toggle item tools menu</span>
    </summary>
    <ul class="header-title-menu__list" role="menu">
      <!-- Navigation link item -->
      <li class="header-title-menu__item" role="none">
        <a href="/admin/some-route" class="header-title-menu__link" role="menuitem">
          Some action
        </a>
      </li>

      <!-- Button item (opens a modal or triggers JS) -->
      <li class="header-title-menu__item" role="none">
        <button
          type="button"
          class="header-title-menu__link"
          role="menuitem"
          data-<action>-open
          aria-haspopup="dialog"
          aria-controls="<modal-id>"
        >
          Another action
        </button>
      </li>

      <!-- Destructive item — add --danger modifier -->
      <li class="header-title-menu__item" role="none">
        <button
          type="submit"
          form="<form-id>"
          class="header-title-menu__link header-title-menu__link--danger"
          role="menuitem"
          disabled
        >
          Delete selected
        </button>
      </li>
    </ul>
  </details>
</div>
```

### Rules

- The `<details>` element is the toggle mechanism — no custom JS is needed to
  open/close the panel; the browser handles it natively.
- The dropdown label (the `<summary>` text) reflects the context of the page,
  e.g. "Ticket tools", "Asset tools", "User tools".
- Destructive actions (delete, bulk-delete, revoke) use
  `header-title-menu__link--danger` and are rendered **last** in the list.
- Bulk-selection actions start with `disabled` and are toggled by per-page JS
  as rows are selected.
- When there are **no secondary actions**, omit the `<details>` element
  entirely and render only the primary button.
- When there is **no primary action** (read-only pages), the dropdown may stand
  alone without a primary button alongside it.
- The `page_header_actions` macro in `templates/macros/header.html` renders the
  same pattern via Jinja; use that macro on pages that do not need the legacy
  `header-title-menu` CSS class.

### CSS classes reference

| Class | Purpose |
|---|---|
| `.header-title-menu` | Flex container holding label + button + dropdown |
| `.header-title-menu__label` | Page title text, grows to fill available width |
| `.header-title-menu__dropdown` | `<details>` wrapper, `position: relative` |
| `.header-title-menu__toggle` | `<summary>` pill — rounded, ghost-style button |
| `.header-title-menu__icon` | Chevron SVG, rotates 180° when open |
| `.header-title-menu__list` | Absolutely positioned dropdown panel |
| `.header-title-menu__item` | `<li>` row inside the panel |
| `.header-title-menu__link` | Interactive row element (link or button) |
| `.header-title-menu__link--danger` | Red variant for destructive items |

---

## 2. Page statistics strip

**Reference implementation:** `/assets` (`app/templates/assets/index.html`)

Aggregate counts and KPIs displayed at the top of a page use a horizontally
tiled **stat strip**.  Each tile shows a short label and a prominent numeric
(or short text) value.  Tile backgrounds are **colour-coded** to communicate
the meaning of the statistic at a glance.

### Using the macro (preferred)

```jinja
{% from "macros/counters.html" import counter_strip %}
{{ counter_strip(
    items=[
        {"label": "Open",      "value": open_count,    "variant": "info"},
        {"label": "Pending",   "value": pending_count, "variant": "warning"},
        {"label": "Overdue",   "value": overdue_count, "variant": "danger"},
        {"label": "Resolved",  "value": closed_count,  "variant": "success"},
    ],
    total=total_count,
    total_label="All items",
) }}
```

The macro emits `.stat-strip` / `.stat-strip__stat` markup with the correct
variant modifier classes applied automatically.

### Colour-coding guide

Choose the variant that best describes the **semantic meaning** of the
statistic, not its raw magnitude:

| Variant | Gradient accent | When to use |
|---|---|---|
| `total` | Sky → blue | Aggregate / "all" counts |
| `success` / `operational` | Green | Healthy, active, resolved, compliant |
| `info` / `maintenance` | Amber-orange | Informational, in-progress, pending |
| `warning` / `degraded` | Yellow | Caution, near-limit, expiring soon |
| `partial_outage` | Orange | Partially failing, partially overdue |
| `danger` / `outage` | Red | Errors, overdue, expired, critical |
| `neutral` | Slate | Uncategorised, unknown, not applicable |

### Structure (manual reference)

```html
<div class="stat-strip" data-stat-strip>
  <!-- Total tile uses --total variant -->
  <div class="stat-strip__stat stat-strip__stat--total">
    <span class="stat-strip__stat-label">Total</span>
    <span class="stat-strip__stat-value">{{ total }}</span>
  </div>

  <!-- Each semantic group uses its own colour variant -->
  <div class="stat-strip__stat stat-strip__stat--success">
    <span class="stat-strip__stat-label">Active</span>
    <span class="stat-strip__stat-value">{{ active_count }}</span>
  </div>

  <div class="stat-strip__stat stat-strip__stat--danger">
    <span class="stat-strip__stat-label">Expired</span>
    <span class="stat-strip__stat-value">{{ expired_count }}</span>
  </div>
</div>
```

Tiles can also be made clickable (linking to a filtered view) by setting
`"href"` on the item dict when using the macro, or by changing the wrapping
element to `<a>` when writing markup by hand.

### Rules

- Always include a **Total** tile as the first tile.
- Every statistic tile must have a **variant** — never leave them all neutral.
- The strip sits directly below the page header, above the first content card,
  and outside any `.card` element.
- Do **not** repeat stat-strip tiles inside card bodies; the strip is a
  page-level element only.
- Use CSS custom properties (`var(--color-…)`) for any additional colour
  overrides — no hard-coded hex values.

---

## 3. Popup modals

**Reference implementation:** Create ticket modal in `/admin/tickets`
(`app/templates/admin/tickets.html`, `id="create-ticket-modal"`)

All dialogs triggered by page actions use a consistent full-viewport overlay
with a scrollable content panel.

### Structure

```html
<div
  class="modal"
  id="<modal-id>"
  role="dialog"
  aria-modal="true"
  aria-labelledby="<modal-title-id>"
  aria-hidden="true"
  hidden
>
  <div class="modal__content" role="document">
    <!-- Close button — always top-right, always first child -->
    <button type="button" class="modal__close" data-modal-close>
      <span class="visually-hidden">Close <descriptive label></span>
      &times;
    </button>

    <!-- Title and optional subtitle -->
    <h2 class="modal__title" id="<modal-title-id>">Modal heading</h2>
    <p class="modal__subtitle">
      One sentence explaining what this modal is for.
    </p>

    <!-- Form (or other content) -->
    <form action="/route" method="post" class="form" autocomplete="off">
      {% include "partials/csrf.html" %}

      <!-- Single-column field -->
      <div class="form-field">
        <label class="form-label" for="field-id">Field label</label>
        <input id="field-id" name="fieldName" class="form-input" />
      </div>

      <!-- Multi-line field -->
      <div class="form-field">
        <label class="form-label" for="textarea-id">Description</label>
        <textarea
          id="textarea-id"
          name="description"
          class="form-input form-input--textarea"
          rows="5"
        ></textarea>
      </div>

      <!-- Two-column row — use .form-grid -->
      <div class="form-grid">
        <div class="form-field">
          <label class="form-label" for="col-a-id">Left field</label>
          <select id="col-a-id" name="colA" class="form-input">
            <option value="a">Option A</option>
          </select>
        </div>
        <div class="form-field">
          <label class="form-label" for="col-b-id">Right field</label>
          <select id="col-b-id" name="colB" class="form-input">
            <option value="b">Option B</option>
          </select>
        </div>
      </div>

      <!-- Form actions — primary submit + ghost cancel -->
      <div class="form-actions">
        <button type="submit" class="button button--primary">Save</button>
        <button type="button" class="button button--ghost" data-modal-close>Cancel</button>
      </div>
    </form>
  </div>
</div>
```

### Opening / closing

Modals are opened and closed by the existing JS in `main.js`.  The trigger
element must carry a `data-<name>-modal-open` attribute and reference the
modal via `aria-controls="<modal-id>"`:

```html
<button
  type="button"
  class="button button--primary"
  data-create-thing-modal-open
  aria-haspopup="dialog"
  aria-controls="create-thing-modal"
>
  New thing
</button>
```

The close button inside the modal uses `data-modal-close`; the Cancel button
in `.form-actions` also carries `data-modal-close`.

### Rules

- Every modal **must** have a `role="dialog"`, `aria-modal="true"`,
  `aria-labelledby`, `aria-hidden="true"`, and `hidden` attribute.
- The `id` of the `<h2 class="modal__title">` must match `aria-labelledby` on
  the outer `.modal` element.
- The `.modal__close` button (×) is always the **first child** of
  `.modal__content` so it remains focusable regardless of content scroll
  position.
- The `.modal__subtitle` is optional but strongly recommended — one sentence
  describing what submitting the form will do.
- Use `.form-grid` for pairs of logically related fields that fit side-by-side
  (e.g. priority + status, start date + end date).
- Forms in modals always include `{% include "partials/csrf.html" %}`.
- `.form-actions` always contains the primary submit button first, then the
  ghost Cancel button last.
- Destructive modals (confirm-delete dialogs) use `button--danger` for the
  submit button instead of `button--primary`.
- Do **not** nest a modal inside a `<form>` element that belongs to the host
  page — the modal's form must be self-contained.
