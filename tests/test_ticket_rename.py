from pathlib import Path


def test_admin_ticket_detail_form_allows_subject_rename():
    template = Path("app/templates/admin/ticket_detail.html").read_text()

    assert 'id="ticket-subject-detail"' in template
    assert 'name="subject"' in template
    assert 'value="{{ ticket.subject or \'\' }}"' in template
    assert "maxlength=\"255\"" in template
    assert "required" in template


def test_admin_ticket_details_route_persists_subject_update():
    route_source = Path("app/features/tickets/admin_routes.py").read_text()

    assert 'subject_value = _clean_text(form.get("subject"))' in route_source
    assert '"subject": subject_value' in route_source
    assert 'error_message="Enter a ticket subject."' in route_source
    assert 'error_message="Subject must be 255 characters or fewer."' in route_source
