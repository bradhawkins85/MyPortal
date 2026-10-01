from pathlib import Path


TEMPLATE = Path("app/templates/admin/service_status.html").read_text()


def test_service_status_page_actions_are_all_in_overflow_menu():
    header_actions = TEMPLATE.split("{% endblock %}", 2)[1]

    assert '"label": "Add service", "type": "button"' in header_actions
    assert '"label": "Published status pages", "type": "button"' in header_actions
    assert '"variant": "primary"' not in header_actions
    assert 'menu_id="service-status-actions"' in header_actions


def test_published_status_pages_table_is_filterable_sortable_modal_content():
    assert 'id="public-pages-modal"' in TEMPLATE
    assert 'aria-labelledby="public-pages-modal-title"' in TEMPLATE
    assert 'data-table-filter="service-status-public-links"' in TEMPLATE
    assert 'id="service-status-public-links" data-table' in TEMPLATE
    assert 'data-sort="string" data-column-key="company"' in TEMPLATE
    assert 'data-sort="string" data-column-key="link"' in TEMPLATE
    assert 'data-public-pages-modal-open' in TEMPLATE
    assert 'data-public-pages-modal-close' in TEMPLATE
