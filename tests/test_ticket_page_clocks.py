from pathlib import Path


def test_ticket_page_clock_is_available_in_reply_form():
    template = Path("app/templates/admin/ticket_detail.html").read_text()

    assert 'data-ticket-page-clock data-ticket-id="{{ ticket.id }}"' in template
    assert "Time open" in template
    assert "data-ticket-page-clock-history" in template


def test_ticket_page_clock_script_records_and_displays_clock_history():
    script = Path("app/static/js/ticket_detail.js").read_text()

    assert "function initialiseTicketPageClock()" in script
    assert "/page-clocks" in script
    assert "navigator.wakeLock.request('screen')" in script
    assert "pagehide" in script


def test_ticket_page_clock_routes_are_registered():
    routes = Path("app/features/tickets/admin_routes.py").read_text()

    assert '"/admin/tickets/{ticket_id:int}/page-clocks"' in routes
    assert '"/admin/tickets/{ticket_id:int}/page-clocks/{clock_id:int}/heartbeat"' in routes
