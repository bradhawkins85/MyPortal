"""Regression checks: the desktop app shell pins the sidebar to full height.

The left sidebar (``nav.layout__sidebar``) must stay a full-height,
viewport-pinned column while the main content scrolls independently inside
``.layout__content``. Without a fixed shell height the document scrolls and the
sidebar scrolls off-screen on long pages. The sidebar keeps ``overflow-y: auto``
so a long menu scrolls within the pinned column. Mobile (off-canvas drawer) and
the top-navigation variant keep normal page scroll and must be excluded from the
pinned-shell rule.
"""

from pathlib import Path

CSS = Path("app/static/css/app.css").read_text()


def _first_rule(selector: str) -> str:
    """Return the first CSS rule (selector + balanced body) whose selector matches."""
    start = CSS.index(selector)
    body_start = CSS.index("{", start)
    depth = 0
    for i in range(body_start, len(CSS)):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start : i + 1]
    raise AssertionError(f"Unbalanced CSS rule for {selector!r}")


def test_desktop_shell_pins_layout_to_viewport_height() -> None:
    rule = _first_rule("body:not(.navigation--top) .layout {")
    assert "height: 100vh" in rule
    # Progressive enhancement for dynamic (mobile) viewports where it applies.
    assert "height: 100dvh" in rule


def test_pinned_shell_is_scoped_to_the_desktop_breakpoint() -> None:
    # The rule must live inside the >=1025px desktop media query so the mobile
    # off-canvas drawer keeps normal page scroll.
    media = _first_rule("@media (min-width: 1025px) {")
    assert "body:not(.navigation--top) .layout {" in media
    assert "height: 100vh" in media


def test_pinned_shell_excludes_top_navigation_variant() -> None:
    # Top navigation uses page scroll with a sticky top bar, so the fixed shell
    # must not apply to it.
    assert "body:not(.navigation--top) .layout {" in CSS


def test_sidebar_stays_full_height_and_scrolls_internally() -> None:
    sidebar = _first_rule(".layout__sidebar {")
    # Scrolls when the menu is long while the column itself stays pinned.
    assert "overflow-y: auto" in sidebar
    # Capped to the viewport so the sidebar never grows the document.
    assert "max-height: 100vh" in sidebar


def test_content_region_is_the_independent_scroller() -> None:
    content = _first_rule(".layout__content {")
    assert "overflow-y: auto" in content
    assert "min-height: 0" in content
    assert "flex: 1" in content
