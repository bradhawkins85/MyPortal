from pathlib import Path


SEARCH_TEMPLATE = Path("app/templates/search.html")
AGENT_SCRIPT = Path("app/static/js/agent.js")
BASE_TEMPLATE = Path("app/templates/base.html")


def test_search_page_uses_friendly_copy_without_header_actions():
    template = SEARCH_TEMPLATE.read_text()

    assert "{% block title %}Search{% endblock %}" in template
    assert '<span class="header__title-text">Search</span>' in template
    assert "How can we assist you" in template
    assert "The assistant searches everything including knowledge base articles" in template
    assert "Possibly Useful Results" in template
    assert "AI Search" not in template
    assert "Search permitted portal content using natural language." not in template
    assert "Results are generated through the configured Ollama" not in template
    assert "{% block header_actions %}" not in template


def test_search_results_open_linked_records_in_new_tabs():
    script = AGENT_SCRIPT.read_text()

    assert "link.href = sourceUrl(sourceType, item);" in script
    assert "link.target = '_blank';" in script
    assert "link.rel = 'noopener noreferrer';" in script
    assert "entry.appendChild(link);" in script


def test_dashboard_link_is_available_to_every_authenticated_user():
    template = BASE_TEMPLATE.read_text()

    assert "{% set can_access_dashboard = has_authenticated_user %}" in template
