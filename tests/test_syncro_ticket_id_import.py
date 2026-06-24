"""
Test that Syncro ticket imports use the Syncro ticket number as the database ID.
"""
import pytest

from app.services import ticket_importer


def test_extract_numeric_ticket_id_from_number():
    """Test extracting numeric ID from ticket 'number' field."""
    ticket = {"id": 999, "number": "12345"}
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 12345


def test_extract_numeric_ticket_id_from_id():
    """Test extracting numeric ID from ticket 'id' field as fallback."""
    ticket = {"id": 54321}
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 54321


def test_extract_numeric_ticket_id_with_non_numeric_number():
    """Test extracting digits from non-numeric ticket numbers."""
    ticket = {"id": 100, "number": "TKT-789"}
    # Should extract digits from the number
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 789


def test_extract_numeric_ticket_id_returns_none_for_invalid():
    """Test that None is returned when no valid ID can be extracted."""
    ticket = {"id": "not-a-number", "number": "ABC"}
    assert ticket_importer._extract_numeric_ticket_id(ticket) is None


def test_extract_numeric_ticket_id_empty_ticket():
    """Test that None is returned for empty ticket."""
    ticket = {}
    assert ticket_importer._extract_numeric_ticket_id(ticket) is None


@pytest.mark.anyio
async def test_syncro_ticket_import_uses_syncro_id(monkeypatch):
    """Test that importing a Syncro ticket uses the Syncro ticket number as the database ID."""
    from app.services import syncro
    from app.services import tickets as tickets_service
    from app.repositories import tickets as tickets_repo
    from app.repositories import companies as company_repo
    from app.repositories import users as user_repo

    async def fake_get_ticket(ticket_id, rate_limiter=None):
        return {
            "id": 12345,
            "number": "12345",
            "subject": "Test ticket",
            "priority": "Normal",
            "status": "Open",
            "problem": "Test description",
            "customer_id": "200",
        }

    async def fake_get_company(syncro_id):
        return {"id": 1}

    async def fake_get_company_by_name(_name):
        return None

    async def fake_get_existing(external_reference):
        return None

    created_ticket_data = {}

    async def fake_create_ticket(**kwargs):
        created_ticket_data.update(kwargs)
        # Return the created ticket with the ID that was passed
        return {"id": kwargs.get("id") or 999, **kwargs}

    async def fake_update_ticket(ticket_id, **fields):
        return {"id": ticket_id, **fields}

    async def fake_get_user_by_email(_email):
        return None

    async def fake_emit_event(*args, **kwargs):
        return None

    async def fake_refresh_summary(ticket_id):
        return None

    async def fake_refresh_tags(ticket_id):
        return None

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", fake_refresh_summary)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", fake_refresh_tags)

    # Import the ticket
    summary = await ticket_importer.import_ticket_by_id(12345, rate_limiter=None)

    # Verify the ticket was created
    assert summary.created == 1
    assert summary.updated == 0
    assert summary.skipped == 0

    # Verify the ID was set to the Syncro ticket number
    assert created_ticket_data.get("id") == 12345
    assert created_ticket_data.get("ticket_number") == "12345"
    assert created_ticket_data.get("external_reference") == "12345"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_syncro_status_mapping_takes_precedence():
    """Configured Syncro statuses map to the selected MyPortal status."""
    assert (
        ticket_importer._normalise_status(
            "Waiting on Customer",
            {"open", "pending", "closed"},
            "open",
            {"waiting on customer": "pending"},
        )
        == "pending"
    )


def test_append_imported_image_markup_replaces_embedded_placeholder():
    attachments = [
        {"id": 42, "mime_type": "image/png", "original_filename": "screenshot.png"},
    ]

    body = ticket_importer._append_imported_image_markup(
        "The time entry included this screenshot: [embedded image]",
        12345,
        attachments,
    )

    assert "[embedded image]" not in body
    assert "/api/tickets/12345/attachments/42/download" in body
    assert 'alt="screenshot.png"' in body
    assert "syncro-embedded-image" in body


@pytest.mark.anyio
async def test_syncro_reply_import_embeds_downloaded_time_entry_images(monkeypatch):
    from app.repositories import tickets as tickets_repo

    created_reply = {}

    async def fake_list_replies(_ticket_id):
        return []

    async def fake_resolve_author(*args, **kwargs):
        return None

    async def fake_import_images(ticket_id, comment, author_id):
        assert ticket_id == 12345
        assert comment["id"] == 987
        assert author_id is None
        return [{"id": 55, "mime_type": "image/jpeg", "original_filename": "photo.jpg"}]

    async def fake_create_reply(**kwargs):
        created_reply.update(kwargs)
        return {"id": 1, **kwargs}

    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(ticket_importer, "_resolve_comment_author_id", fake_resolve_author)
    monkeypatch.setattr(ticket_importer, "_import_comment_images", fake_import_images)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)

    await ticket_importer._sync_ticket_replies(
        12345,
        [
            {
                "id": 987,
                "body": "Worked on issue [embedded image]",
                "time_worked": "00:15:00",
            }
        ],
        requester_id=None,
        contact_email=None,
    )

    assert created_reply["ticket_id"] == 12345
    assert created_reply["external_reference"] == "987"
    assert created_reply["minutes_spent"] == 15
    assert "[embedded image]" not in created_reply["body"]
    assert "/api/tickets/12345/attachments/55/download" in created_reply["body"]
