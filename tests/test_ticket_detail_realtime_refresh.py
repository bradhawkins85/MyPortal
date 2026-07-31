from pathlib import Path


def test_admin_ticket_detail_identifies_itself_to_realtime_refresh_handler():
    template = Path("app/templates/admin/ticket_detail.html").read_text()

    assert "data-admin-ticket-detail" in template
    assert 'data-ticket-id="{{ ticket.id }}"' in template


def test_new_ticket_refresh_does_not_reload_an_open_admin_ticket():
    script = Path("app/static/js/main.js").read_text()

    assert "function shouldIgnoreRefresh(payload)" in script
    assert "document.querySelector('[data-admin-ticket-detail]')" in script
    assert "topics.includes('tickets')" in script
    assert "action === 'create' || action === 'created'" in script
    assert "if (shouldIgnoreRefresh(payload))" in script
