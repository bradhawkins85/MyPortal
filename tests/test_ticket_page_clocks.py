from pathlib import Path


def test_ticket_page_clock_history_is_available_from_the_header_actions_menu():
    template = Path("app/templates/admin/ticket_detail.html").read_text()

    assert 'data-ticket-page-clock data-ticket-id="{{ ticket.id }}"' in template
    assert "Time open" in template
    assert '"label": "Clock history"' in template
    assert '"data-ticket-page-clock-history": true' in template
    assert 'clock.querySelector(\'[data-ticket-page-clock-history]\')' not in Path(
        "app/static/js/ticket_detail.js"
    ).read_text()


def test_ticket_page_clock_script_records_and_displays_clock_history():
    script = Path("app/static/js/ticket_detail.js").read_text()

    assert "function initialiseTicketPageClock()" in script
    assert "/page-clocks" in script
    assert "navigator.wakeLock.request('screen')" in script
    assert "pagehide" in script


def test_ticket_clock_history_uses_the_users_name_columns():
    repository = Path("app/repositories/ticket_clocks.py").read_text()

    assert "u.display_name" not in repository
    assert "u.first_name" in repository
    assert "u.last_name" in repository


def test_ticket_page_clock_routes_are_registered():
    routes = Path("app/features/tickets/admin_routes.py").read_text()

    assert '"/admin/tickets/{ticket_id:int}/page-clocks"' in routes
    assert '"/admin/tickets/{ticket_id:int}/page-clocks/{clock_id:int}/heartbeat"' in routes
