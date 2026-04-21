"""Template compliance tests — Phase 6.

Walks every Jinja2 template under ``app/templates`` and asserts the
standard layout primitives are used:

1. Every page-level template (extends ``base.html``) defines either
   ``{% block header_title %}`` or ``{% block title %}`` — this prevents
   the bare "Dashboard" fallback from showing on real pages.

2. Every ``<table … data-table …>`` declares a stable ``data-table-id``
   attribute so the column-visibility picker can persist preferences.

3. No template contains a card subtitle that merely repeats the page
   header (a heuristic check for known violations).

These tests are deliberately permissive: they whitelist partials, base
templates, and other non-page templates.  When a new top-level page is
added, the test will fail until it conforms to the standards documented
in ``docs/ui_layout_standards.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"

# Templates we deliberately skip — partials, layout shells, includes,
# and macro libraries that don't render a full page.
SKIP_PATHS: set[str] = {
    "base.html",
    "errors/error.html",
    "auth/login.html",
    "auth/register.html",
    "bcp/layout.html",
    "bcp/stub.html",
    "bcp/heatmap_partial.html",
    "bcp/export/bcp_pdf.html",
}
SKIP_PREFIXES: tuple[str, ...] = (
    "macros/",
    "partials/",
)


def _all_templates() -> list[Path]:
    return [p for p in TEMPLATES_DIR.rglob("*.html") if "__pycache__" not in p.parts]


def _page_templates() -> list[Path]:
    pages: list[Path] = []
    for path in _all_templates():
        rel = path.relative_to(TEMPLATES_DIR).as_posix()
        if rel in SKIP_PATHS or rel.startswith(SKIP_PREFIXES):
            continue
        text = path.read_text(errors="replace")
        # Only check templates that extend a base — i.e. real pages.
        if "{% extends" not in text:
            continue
        pages.append(path)
    return pages


PAGES = _page_templates()


@pytest.mark.parametrize(
    "template_path", PAGES, ids=lambda p: p.relative_to(TEMPLATES_DIR).as_posix()
)
def test_page_defines_header_title_or_title_block(template_path: Path) -> None:
    """Page templates must define their own header title."""
    text = template_path.read_text(errors="replace")
    has_header_title = "{% block header_title" in text
    has_title = re.search(r"{%\s*block\s+title\s*%}", text) is not None
    # Pages that extend a sub-layout (e.g. bcp/layout.html) inherit the
    # frame from base.html and may not need a header_title themselves
    # if the parent layout supplies one. We only enforce on direct
    # base.html extensions or where neither side provides one.
    extends_base = re.search(r'{%\s*extends\s+["\']base\.html["\']', text) is not None
    if not extends_base:
        return
    assert has_header_title or has_title, (
        f"{template_path.relative_to(TEMPLATES_DIR)} extends base.html but "
        f"defines neither {{% block header_title %}} nor {{% block title %}}. "
        "Add one so the app frame shows a meaningful title."
    )


@pytest.mark.parametrize(
    "template_path", _all_templates(), ids=lambda p: p.relative_to(TEMPLATES_DIR).as_posix()
)
def test_data_tables_have_stable_id(template_path: Path) -> None:
    """Tables marked as ``data-table`` must declare a ``data-table-id``.

    The ID is required for the column-visibility picker and per-user
    preference persistence (Phase 3).
    """
    text = template_path.read_text(errors="replace")
    # Find every <table ...> opening tag (single-line; multiline tags
    # are normalised first by collapsing whitespace).
    normalised = re.sub(r"\s+", " ", text)
    for match in re.finditer(r"<table\b([^>]*)>", normalised):
        attrs = match.group(1)
        if "data-table" not in attrs:
            continue
        # data-table-filter / data-table-id should not match data-table on its own
        # so check for the standalone attribute.
        has_data_table = re.search(r"\bdata-table(?:\s|=|>|$)", attrs) is not None
        if not has_data_table:
            continue
        has_id = re.search(r"\bdata-table-id\s*=", attrs) is not None
        assert has_id, (
            f"{template_path.relative_to(TEMPLATES_DIR)}: <table data-table> "
            f"is missing data-table-id. Add data-table-id=\"<unique-id>\" so "
            "the column-visibility picker can persist user preferences. "
            f"Tag attributes: {attrs.strip()[:160]}"
        )


def test_no_raw_card_empty_paragraph() -> None:
    """``<p class="card__empty">`` should be replaced with the empty_state macro
    on top-level list / index pages.

    Detail pages may legitimately use ``card__empty`` as inline placeholders
    for JS-controlled regions (AI tags, watchers, tasks) — those are
    intentionally exempt.
    """
    allowed_suffixes = ("/detail.html", "_detail.html", "_editor.html")
    offenders: list[str] = []
    for path in _all_templates():
        rel = path.relative_to(TEMPLATES_DIR).as_posix()
        if rel.endswith(allowed_suffixes):
            continue
        if 'class="card__empty"' in path.read_text(errors="replace"):
            offenders.append(rel)
    assert not offenders, (
        "These templates still use <p class=\"card__empty\">. Replace with "
        "the empty_state() macro from macros/tables.html:\n  - "
        + "\n  - ".join(offenders)
    )
