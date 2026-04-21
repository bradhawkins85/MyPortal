# Mobile UX Guidelines

MyPortal is primarily a desktop product, but it must remain comfortably usable on
phones and tablets via the existing PWA. These guidelines describe the standard
breakpoints and primitives any new page should use so that the mobile experience
stays consistent and uncluttered. Implementation lives in
`app/static/css/app.css` and `app/templates/macros/mobile.html`.

## Standard breakpoints

| Bucket  | Range                | Notes                                                |
| ------- | -------------------- | ---------------------------------------------------- |
| mobile  | `max-width: 640px`   | Phones (portrait or landscape).                      |
| tablet  | `max-width: 1024px`  | Tablets and small laptops; sidebar is off-canvas.    |
| desktop | `min-width: 1025px`  | Default 3-part layout, full feature set visible.     |

Always use these three buckets. **Do not invent new breakpoints** for a single
page — instead, apply the utility classes below or extend the shared CSS.

## CSS utility classes

Defined at the top of `app/static/css/app.css`. They are `!important` so they
win over component styles; use them on any element you want to hide or show
based on viewport.

| Class                  | Behaviour                                       |
| ---------------------- | ----------------------------------------------- |
| `u-hide-mobile`        | Hidden ≤640 px                                  |
| `u-only-mobile`        | Visible only ≤640 px                            |
| `u-hide-tablet`        | Hidden ≤1024 px                                 |
| `u-priority-low`       | Hidden ≤640 px (semantic alias for "phone-skip")|
| `u-priority-secondary` | Hidden ≤480 px (small-phone trim)               |

There is also the `data-mobile-hidden` attribute used by `tables.js`:

- `data-mobile-hidden="true"` → hidden ≤640 px in any orientation.
- `data-mobile-hidden="portrait"` → opt-in; only hides at ≤720 px portrait.

## Tables: prefer `table--stack-mobile`

Any data table with `<td data-label="…">` cells (most tables in the app already
have this) can opt into a card-stack layout on phones by adding the
`table--stack-mobile` modifier:

```html
<table class="table table--stack-mobile" data-table>
  <thead> … </thead>
  <tbody>
    <tr>
      <td data-label="Invoice #"> … </td>
      <td data-label="Amount"> … </td>
    </tr>
  </tbody>
</table>
```

Under 640 px the table reflows into one card per row, with the `data-label` text
shown to the left of each value. No JS required; desktop is unchanged.

If instead you want to hide specific columns on phones (rather than reflow),
mark headers with `data-mobile-priority="supporting"`. The `tables.js`
controller will set `data-mobile-hidden="true"` on those cells under 640 px.
Combine with the global `data-mobile-hidden` CSS to keep them out of view.

> **Pick one or the other.** `table--stack-mobile` already shows every column
> as a label/value row; combining it with `data-mobile-priority="supporting"`
> will hide those rows from the card.

## Header actions: collapse to a "More" menu

Pages with three or more header buttons should use the `header_actions_overflow`
macro so secondary actions collapse behind a "⋯" menu on phones:

```jinja
{% from 'macros/mobile.html' import header_actions_overflow %}

{% block header_actions %}
  {% call(slot) header_actions_overflow() %}
    {% if slot == 'primary' %}
      <button class="button button--primary">New ticket</button>
    {% elif slot == 'secondary' %}
      <a class="button button--ghost" href="/export">Export</a>
      <a class="button button--ghost" href="/import">Import</a>
    {% endif %}
  {% endcall %}
{% endblock %}
```

The toggle button, click-outside handling, and Escape key are all wired up by
`app/static/js/viewport.js` — no per-page script needed.

## Collapsible mobile sections

For long property panels, advanced filter forms, or other "second-tier" content,
wrap with `mobile_collapsible`:

```jinja
{% from 'macros/mobile.html' import mobile_collapsible %}

{% call mobile_collapsible('Advanced filters', open=False) %}
  …form fields…
{% endcall %}
```

This renders a native `<details>`/`<summary>`. On desktop (≥641 px) the panel
stays expanded and the summary chip is hidden; on phones the section collapses
into a tap-to-expand chip.

## JavaScript: the viewport helper

`app/static/js/viewport.js` is loaded globally from `base.html`. It provides:

```js
window.MyPortal.viewport.current      // 'mobile' | 'tablet' | 'desktop'
window.MyPortal.viewport.isMobile()
window.MyPortal.viewport.isTablet()   // true for mobile or tablet
window.MyPortal.viewport.isDesktop()
```

Subscribe to bucket changes:

```js
window.addEventListener('viewport:change', (event) => {
  // event.detail = { previous: 'desktop', current: 'mobile' }
});
```

Use this when JS state needs to differ per viewport — for example, persisting a
user's preferred *mobile* set of visible columns separately from desktop.

## Mobile checklist for new pages

1. Test at 360 px, 414 px, 768 px, 1024 px, 1280 px in DevTools.
2. No horizontal scrolling at 360–414 px.
3. Header has at most one *primary* button visible on mobile; the rest go in
   the overflow menu.
4. Data tables either fit on screen, use `table--stack-mobile`, or hide
   non-essential columns with `data-mobile-priority="supporting"`.
5. Long forms / "advanced" sections hide behind `mobile_collapsible`.
6. Tap targets for buttons / links inside tables and action bars are at least
   44 × 44 px (handled automatically by the global rule for `.button` inside
   `.table`, `.table__actions`, `.header__actions`, `.header-actions`).
7. Sidebar and overflow menus close after navigation.
