"""Test Trello automation action module."""
import pytest
from unittest.mock import AsyncMock

from app.services import modules


@pytest.fixture
def mock_webhook_monitor(monkeypatch):
    """Mock webhook_monitor.create_manual_event to return a valid event."""
    mock_event = {"id": 1, "status": "pending"}
    mock_create = AsyncMock(return_value=mock_event)
    monkeypatch.setattr("app.services.modules.webhook_monitor.create_manual_event", mock_create)
    return mock_create


@pytest.mark.asyncio
async def test_trello_module_is_triggerable():
    """Test that the trello module is not in the non-triggerable set."""
    from app.services.modules import _NON_TRIGGERABLE_MODULE_SLUGS
    assert "trello" not in _NON_TRIGGERABLE_MODULE_SLUGS


@pytest.mark.asyncio
async def test_trello_payload_schema_exists():
    """Test that the trello module has a payload schema defined."""
    schema = modules.get_action_payload_schema("trello")
    assert schema is not None
    fields = {f["name"] for f in schema["fields"]}
    assert "card_id" in fields
    assert "text" in fields


@pytest.mark.asyncio
async def test_trello_default_module_enabled():
    """Test that the trello module has enabled=True in DEFAULT_MODULES."""
    from app.services.modules import DEFAULT_MODULES
    trello = next((m for m in DEFAULT_MODULES if m["slug"] == "trello"), None)
    assert trello is not None
    assert trello.get("enabled") is True


@pytest.mark.asyncio
async def test_invoke_trello_add_comment_success(monkeypatch):
    """Test that _invoke_trello_add_comment posts a comment successfully."""
    mock_add_comment = AsyncMock(return_value={"id": "abc123"})
    monkeypatch.setattr("app.services.trello.add_comment_to_card", mock_add_comment)

    result = await modules._invoke_trello_add_comment(
        {},
        {
            "card_id": "card123",
            "text": "Ticket updated",
        },
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert result["card_id"] == "card123"
    assert result["comment_id"] == "abc123"
    mock_add_comment.assert_called_once()


@pytest.mark.asyncio
async def test_invoke_trello_add_comment_missing_card_id():
    """Test that _invoke_trello_add_comment raises when card_id is missing."""
    with pytest.raises(ValueError, match="card_id is required"):
        await modules._invoke_trello_add_comment(
            {},
            {"text": "Some comment"},
            event_future=None,
        )


@pytest.mark.asyncio
async def test_invoke_trello_add_comment_missing_text():
    """Test that _invoke_trello_add_comment raises when text is missing."""
    with pytest.raises(ValueError, match="text is required"):
        await modules._invoke_trello_add_comment(
            {},
            {"card_id": "card123"},
            event_future=None,
        )


@pytest.mark.asyncio
async def test_invoke_trello_add_comment_api_failure_returns_skipped(monkeypatch):
    """Test that _invoke_trello_add_comment returns skipped when API returns None."""
    mock_add_comment = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.trello.add_comment_to_card", mock_add_comment)

    result = await modules._invoke_trello_add_comment(
        {},
        {"card_id": "card123", "text": "Hello"},
        event_future=None,
    )

    assert result["status"] == "skipped"
    assert result["card_id"] == "card123"


@pytest.mark.asyncio
async def test_invoke_trello_add_comment_resolves_company_from_context(monkeypatch):
    """Test that _invoke_trello_add_comment passes company from ticket context."""
    captured_kwargs = {}

    async def fake_add_comment(card_id, text, *, company=None):
        captured_kwargs["company"] = company
        return {"id": "xyz"}

    monkeypatch.setattr("app.services.trello.add_comment_to_card", fake_add_comment)

    company_record = {"id": 5, "name": "Acme", "trello_api_key": "key", "trello_token": "tok"}
    context = {"ticket": {"id": 1, "company": company_record}}

    result = await modules._invoke_trello_add_comment(
        {},
        {"card_id": "cardABC", "text": "Comment text", "context": context},
        event_future=None,
    )

    assert result["status"] == "succeeded"
    assert captured_kwargs["company"] == company_record


@pytest.mark.asyncio
async def test_trello_is_included_in_trigger_action_modules(monkeypatch):
    """Test that trello appears in list_trigger_action_modules when enabled."""
    from app.repositories import integration_modules as module_repo

    async def fake_list_modules():
        return [
            {"slug": "trello", "name": "Trello", "enabled": True, "settings": {}},
            {"slug": "imap", "name": "IMAP", "enabled": True, "settings": {}},
        ]

    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)

    result = await modules.list_trigger_action_modules()
    result_slugs = {m["slug"] for m in result}

    assert "trello" in result_slugs
    assert "imap" not in result_slugs

    # Verify the payload_schema is attached
    trello_entry = next(m for m in result if m["slug"] == "trello")
    assert trello_entry["payload_schema"] is not None
    assert any(f["name"] == "card_id" for f in trello_entry["payload_schema"]["fields"])
