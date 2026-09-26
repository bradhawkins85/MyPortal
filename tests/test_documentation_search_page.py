"""Browser-contract coverage for the documentation search workspace."""

from pathlib import Path


TEMPLATE = Path("app/templates/documentation_search.html")
SCRIPT = Path("app/static/js/documentation_search.js")
BASE = Path("app/templates/base.html")


def test_navigation_exposes_documentation_search_to_authenticated_users():
    navigation = BASE.read_text(encoding="utf-8")

    assert 'href="/documentation-search"' in navigation
    assert "Documentation search" in navigation
    assert "{% if has_authenticated_user %}" in navigation


def test_search_page_exposes_accessible_filters_and_responsive_result_regions():
    page = TEMPLATE.read_text(encoding="utf-8")

    assert 'role="search"' in page
    assert 'type="search"' in page
    assert 'data-documentation-company' in page
    assert '>Source<' in page
    assert '>Record type<' in page
    assert '>Status<' in page
    assert '>Owner<' in page
    assert 'role="status" aria-live="polite"' in page
    assert 'aria-label="Search result pages"' in page
    assert 'data-documentation-clear' in page


def test_browser_flow_preserves_query_and_uses_canonical_record_links():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "fetch('/api/documentation/search?'" in script
    assert "window.history.replaceState" in script
    assert "initial.get('q')" in script
    assert "^\\/assets\\/" in script
    assert "^\\/knowledge-base\\/articles\\/" in script
    assert "title.textContent" in script
    assert "snippet.textContent" in script
    assert "currentPage - 1" in script
    assert "currentPage + 1" in script


def test_browser_flow_has_non_disclosing_empty_and_error_states():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "No accessible documentation matched your search." in script
    assert "Documentation search is unavailable right now." in script
    assert "results.hidden = true" in script
    assert "response.statusText" not in script
    assert "error.message" not in script
