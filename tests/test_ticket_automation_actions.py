"""Test ticket automation action modules."""
import json
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services import modules


@pytest.fixture
def mock_webhook_monitor(monkeypatch):
    """Mock webhook_monitor.create_manual_event to return a valid event."""
    mock_event = {
        "id": 1,
        "status": "pending",
    }
    mock_create = AsyncMock(return_value=mock_event)
    monkeypatch.setattr("app.services.modules.webhook_monitor.create_manual_event", mock_create)
    return mock_create


@pytest.fixture
def mock_record_success(monkeypatch):
    """Mock _record_success to return a success event."""
    async def fake_record_success(event_id, *, attempt_number, response_status, response_body):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": response_status,
            "response_body": response_body,
        }
    monkeypatch.setattr("app.services.modules._record_success", fake_record_success)


@pytest.fixture
def mock_record_failure(monkeypatch):
    """Mock _record_failure to return a failure event."""
    async def fake_record_failure(event_id, *, attempt_number, status, error_message, response_status, response_body):
        return {
            "id": event_id,
            "status": status,
            "last_error": error_message,
        }
    monkeypatch.setattr("app.services.modules._record_failure", fake_record_failure)


@pytest.mark.asyncio
async def test_invoke_update_ticket_changes_status(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that update-ticket module can change ticket status."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    
    # Mock ticket exists
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "status": "open", "priority": "normal"}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    # Mock update_ticket
    async def fake_update_ticket(ticket_id, **kwargs):
        return {"id": ticket_id, **kwargs}
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    
    # Mock emit_ticket_updated_event
    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)
    
    # Call the handler
    result = await modules._invoke_update_ticket(
        {},
        {"ticket_id": 1, "status": "resolved"},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["ticket_id"] == 1
    assert "status" in result["updated_fields"]


@pytest.mark.asyncio
async def test_invoke_update_ticket_normalises_status(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Status values are normalised to slug format for consistency."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "status": "open"}

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)

    captured_updates: dict[str, Any] = {}

    async def fake_update_ticket(ticket_id, **kwargs):
        captured_updates.update(kwargs)
        return {"id": ticket_id, **kwargs}

    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)

    result = await modules._invoke_update_ticket(
        {},
        {"ticket_id": 7, "status": "Customer Replied"},
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert captured_updates.get("status") == "customer_replied"


@pytest.mark.asyncio
async def test_invoke_update_ticket_from_context(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that update-ticket module can get ticket_id from context."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "status": "open"}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    async def fake_update_ticket(ticket_id, **kwargs):
        return {"id": ticket_id, **kwargs}
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    
    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)
    
    # Call with ticket_id in context
    result = await modules._invoke_update_ticket(
        {},
        {"context": {"ticket": {"id": 42}}, "priority": "high"},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["ticket_id"] == 42


@pytest.mark.asyncio
async def test_invoke_update_ticket_no_fields_skips(monkeypatch, mock_webhook_monitor):
    """Test that update-ticket returns skipped when no fields provided."""
    from app.repositories import tickets as tickets_repo
    
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "status": "open"}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    result = await modules._invoke_update_ticket(
        {},
        {"ticket_id": 1},  # No update fields
        event_future=None,
    )
    
    assert result["status"] == "skipped"
    assert "No update fields" in result["reason"]


@pytest.mark.asyncio
async def test_invoke_update_ticket_missing_ticket_id():
    """Test that update-ticket raises error when ticket_id is missing."""
    with pytest.raises(ValueError, match="ticket_id is required"):
        await modules._invoke_update_ticket({}, {}, event_future=None)


@pytest.mark.asyncio
async def test_smart_attachment_removal_deletes_duplicate_files(monkeypatch, tmp_path):
    """Duplicate attachment files are removed from storage and the attachment list."""
    from app.repositories import tickets as tickets_repo
    from app.repositories import ticket_attachments as attachments_repo
    from app.services import ticket_attachments as attachments_service

    attachments = [
        {
            "id": 1,
            "ticket_id": 10,
            "filename": "first.png",
            "original_filename": "logo.png",
        },
        {
            "id": 2,
            "ticket_id": 10,
            "filename": "duplicate.png",
            "original_filename": "logo-copy.png",
        },
        {
            "id": 3,
            "ticket_id": 10,
            "filename": "unique.png",
            "original_filename": "diagram.png",
        },
    ]
    (tmp_path / "first.png").write_bytes(b"same-image-bytes")
    (tmp_path / "duplicate.png").write_bytes(b"same-image-bytes")
    (tmp_path / "unique.png").write_bytes(b"different-image-bytes")

    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 10}))
    monkeypatch.setattr(
        attachments_repo, "list_attachments", AsyncMock(return_value=attachments)
    )
    monkeypatch.setattr(
        attachments_service,
        "get_attachment_file_path",
        lambda filename: tmp_path / filename,
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr(attachments_service, "delete_attachment_file", delete_mock)

    result = await modules._invoke_smart_attachment_removal(
        {},
        {"context": {"ticket": {"id": 10}}},
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert result["scanned"] == 3
    assert result["unique"] == 2
    assert result["duplicates_found"] == 1
    assert result["removed"] == 1
    delete_mock.assert_awaited_once_with(attachments[1])


@pytest.mark.asyncio
async def test_smart_attachment_removal_dry_run_preserves_duplicates(monkeypatch, tmp_path):
    """Dry runs report duplicates without deleting attachment files."""
    from app.repositories import tickets as tickets_repo
    from app.repositories import ticket_attachments as attachments_repo
    from app.services import ticket_attachments as attachments_service

    attachments = [
        {"id": 1, "ticket_id": 10, "filename": "a.png", "original_filename": "a.png"},
        {"id": 2, "ticket_id": 10, "filename": "b.png", "original_filename": "b.png"},
    ]
    (tmp_path / "a.png").write_bytes(b"signature")
    (tmp_path / "b.png").write_bytes(b"signature")

    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 10}))
    monkeypatch.setattr(
        attachments_repo, "list_attachments", AsyncMock(return_value=attachments)
    )
    monkeypatch.setattr(
        attachments_service,
        "get_attachment_file_path",
        lambda filename: tmp_path / filename,
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr(attachments_service, "delete_attachment_file", delete_mock)

    result = await modules._invoke_smart_attachment_removal(
        {},
        {"ticket_id": 10, "hash_algorithm": "md5", "dry_run": True},
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert result["hash_algorithm"] == "md5"
    assert result["duplicates_found"] == 1
    assert result["removed"] == 0
    delete_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_smart_attachment_removal_hashes_canonical_svg_content(
    monkeypatch, tmp_path
):
    """SVG attachments with the same XML content are matched despite formatting."""
    from app.repositories import tickets as tickets_repo
    from app.repositories import ticket_attachments as attachments_repo
    from app.services import ticket_attachments as attachments_service

    attachments = [
        {
            "id": 9965,
            "ticket_id": 25063,
            "filename": "first.svg",
            "original_filename": "qr-code (1).svg",
            "mime_type": "image/svg+xml",
        },
        {
            "id": 9967,
            "ticket_id": 25063,
            "filename": "duplicate.svg",
            "original_filename": "qr-code (1).svg",
            "mime_type": "image/svg+xml",
        },
    ]
    (tmp_path / "first.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect height="10" width="10"></rect></svg>',
        encoding="utf-8",
    )
    (tmp_path / "duplicate.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">\n'
        '  <rect height="10" width="10"></rect>\n'
        "</svg>\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 25063}))
    monkeypatch.setattr(
        attachments_repo, "list_attachments", AsyncMock(return_value=attachments)
    )
    monkeypatch.setattr(
        attachments_service,
        "get_attachment_file_path",
        lambda filename: tmp_path / filename,
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr(attachments_service, "delete_attachment_file", delete_mock)

    result = await modules._invoke_smart_attachment_removal(
        {},
        {"ticket_id": 25063},
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert result["unique"] == 1
    assert result["duplicates_found"] == 1
    assert result["duplicates"][0]["hash_type"] == "svg-c14n"
    delete_mock.assert_awaited_once_with(attachments[1])


@pytest.mark.asyncio
async def test_invoke_update_ticket_description(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that update-ticket-description module changes description."""
    from app.services import tickets as tickets_service
    
    mock_update = AsyncMock(return_value={"id": 1, "description": "New description"})
    monkeypatch.setattr(tickets_service, "update_ticket_description", mock_update)
    
    result = await modules._invoke_update_ticket_description(
        {},
        {"ticket_id": 1, "description": "New description"},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["description_updated"] is True


@pytest.mark.asyncio
async def test_invoke_update_ticket_description_missing_description():
    """Test that update-ticket-description raises error when description is missing."""
    with pytest.raises(ValueError, match="description is required"):
        await modules._invoke_update_ticket_description(
            {},
            {"ticket_id": 1},  # No description
            event_future=None,
        )


@pytest.mark.asyncio
async def test_invoke_reprocess_ai_summary_and_tags(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that reprocess-ai module triggers both summary and tags refresh."""
    from app.services import tickets as tickets_service
    
    mock_summary = AsyncMock()
    mock_tags = AsyncMock()
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", mock_summary)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", mock_tags)
    
    result = await modules._invoke_reprocess_ai(
        {},
        {"ticket_id": 1, "refresh_summary": True, "refresh_tags": True},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert "summary" in result["processed"]
    assert "tags" in result["processed"]
    mock_summary.assert_called_once_with(1)
    mock_tags.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_invoke_reprocess_ai_summary_only(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that reprocess-ai can refresh only summary."""
    from app.services import tickets as tickets_service
    
    mock_summary = AsyncMock()
    mock_tags = AsyncMock()
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", mock_summary)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", mock_tags)
    
    result = await modules._invoke_reprocess_ai(
        {},
        {"ticket_id": 1, "refresh_summary": True, "refresh_tags": False},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["processed"] == ["summary"]
    mock_summary.assert_called_once()
    mock_tags.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_reprocess_ai_no_processing_skips(monkeypatch, mock_webhook_monitor):
    """Test that reprocess-ai returns skipped when both options are false."""
    result = await modules._invoke_reprocess_ai(
        {},
        {"ticket_id": 1, "refresh_summary": False, "refresh_tags": False},
        event_future=None,
    )
    
    assert result["status"] == "skipped"
    assert "No AI processing" in result["reason"]


@pytest.mark.asyncio
async def test_invoke_add_ticket_reply_public(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that add-ticket-reply creates a public reply."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    async def fake_create_reply(**kwargs):
        return {"id": 100, **kwargs}
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    
    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)
    
    result = await modules._invoke_add_ticket_reply(
        {},
        {
            "ticket_id": 1,
            "body": "<p>Thank you for contacting us.</p>",
            "is_internal": False,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["reply_id"] == 100
    assert result["is_internal"] is False


@pytest.mark.asyncio
async def test_invoke_add_ticket_reply_internal_note(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that add-ticket-reply creates an internal note."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    async def fake_create_reply(**kwargs):
        return {"id": 101, **kwargs}
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    
    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)
    
    result = await modules._invoke_add_ticket_reply(
        {},
        {
            "ticket_id": 1,
            "body": "Internal investigation note",
            "is_internal": True,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result["is_internal"] is True


@pytest.mark.asyncio
async def test_invoke_add_ticket_reply_with_time_tracking(monkeypatch, mock_webhook_monitor, mock_record_success):
    """Test that add-ticket-reply can include billable time."""
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service
    
    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id}
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    
    captured_kwargs = {}
    async def fake_create_reply(**kwargs):
        captured_kwargs.update(kwargs)
        return {"id": 102, **kwargs}
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    
    mock_emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", mock_emit)
    
    result = await modules._invoke_add_ticket_reply(
        {},
        {
            "ticket_id": 1,
            "body": "Completed troubleshooting session",
            "is_internal": False,
            "minutes_spent": 30,
            "is_billable": True,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert captured_kwargs["minutes_spent"] == 30
    assert captured_kwargs["is_billable"] is True


@pytest.mark.asyncio
async def test_invoke_add_ticket_reply_missing_body():
    """Test that add-ticket-reply raises error when body is missing."""
    with pytest.raises(ValueError, match="body is required"):
        await modules._invoke_add_ticket_reply(
            {},
            {"ticket_id": 1},  # No body
            event_future=None,
        )


@pytest.mark.asyncio
async def test_invoke_add_ticket_reply_empty_body():
    """Test that add-ticket-reply raises error when body is empty."""
    with pytest.raises(ValueError, match="body is required"):
        await modules._invoke_add_ticket_reply(
            {},
            {"ticket_id": 1, "body": "   "},
            event_future=None,
        )
