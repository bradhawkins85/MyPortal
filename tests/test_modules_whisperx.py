"""Tests for the WhisperX automation module handler."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.services import modules


async def _noop(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, body: dict | str, status_code: int = 200):
        self.status_code = status_code
        if isinstance(body, dict):
            self._text = json.dumps(body)
        else:
            self._text = body
        self.request = httpx.Request("POST", "http://whisperx.local/asr")
        self.headers = {"content-type": "application/json"}

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=self.request, response=self,
            )


class _AsyncClientFactory:
    """Capture POST calls instead of making real HTTP requests."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, files=None, data=None, headers=None):
        self.calls.append({"url": url, "files": files, "data": data, "headers": headers})
        return self._response


def _webhook_helpers(monkeypatch):
    """Wire up fake webhook monitoring helpers shared by all tests."""
    fake_event_state = {"id": 99, "status": "pending", "attempt_count": 0}

    async def fake_create_event(**kwargs):
        return dict(fake_event_state)

    async def fake_record_attempt(**kwargs):
        fake_event_state["attempt_count"] = kwargs.get("attempt_number", 1)

    async def fake_mark_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            status="succeeded",
            attempt_count=attempt_number,
            response_status=response_status,
            response_body=response_body,
        )

    async def fake_mark_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        fake_event_state.update(
            status="failed",
            attempt_count=attempt_number,
            last_error=error_message,
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)

    return fake_event_state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_invoke_whisperx_transcribes_and_adds_note(monkeypatch, tmp_path):
    """Happy path: one WAV attachment → transcription → internal note."""
    fake_event_state = _webhook_helpers(monkeypatch)

    # Fake ticket
    async def fake_get_ticket(tid):
        return {"id": tid, "subject": "Voicemail"}

    # Fake attachment list – one WAV
    audio_file = tmp_path / "abc123.wav"
    audio_file.write_bytes(b"RIFF fake wav content")

    async def fake_list_attachments(tid):
        return [
            {
                "id": 1,
                "ticket_id": tid,
                "filename": "abc123.wav",
                "original_filename": "voicemail.wav",
                "mime_type": "audio/wav",
                "file_size": 1024,
            }
        ]

    # Fake reply creation
    created_reply = {}

    async def fake_create_reply(*, ticket_id, author_id, body, is_internal, **kw):
        created_reply.update(ticket_id=ticket_id, body=body, is_internal=is_internal)
        return {"id": 42}

    async def fake_emit_event(tid, *, actor_type, trigger_automations):
        pass

    # Patch imports inside handler (lazy imports)
    import app.repositories.tickets as tickets_repo_mod
    import app.repositories.ticket_attachments as attach_repo_mod

    monkeypatch.setattr(tickets_repo_mod, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(attach_repo_mod, "list_attachments", fake_list_attachments)
    monkeypatch.setattr(tickets_repo_mod, "create_reply", fake_create_reply)
    monkeypatch.setattr(modules.tickets_service, "emit_ticket_updated_event", fake_emit_event)

    # Fake HTTP client
    whisperx_response = _FakeResponse({"text": "Hello this is a voicemail message."})
    client_factory = _AsyncClientFactory(whisperx_response)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    # Place file in the expected upload directory
    upload_dir = Path(modules.__file__).parent.parent / "static" / "uploads" / "tickets"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_file = upload_dir / "abc123.wav"
    created_target = False
    if not target_file.exists():
        target_file.write_bytes(b"RIFF fake wav content")
        created_target = True

    try:
        settings = {"base_url": "http://whisperx.local", "api_key": "test-key", "language": "en"}
        payload = {"ticket_id": 10}

        result = asyncio.run(modules._invoke_whisperx(settings, payload))

        # Verify result
        assert result["status"] == "succeeded"
        assert result["ticket_id"] == 10
        assert result["transcription_count"] == 1
        assert result["reply_id"] == 42

        # Verify the reply was created as internal note
        assert created_reply["ticket_id"] == 10
        assert created_reply["is_internal"] is True
        assert "Hello this is a voicemail message." in created_reply["body"]
        assert "voicemail.wav" in created_reply["body"]

        # Verify HTTP call
        assert len(client_factory.calls) == 1
        call = client_factory.calls[0]
        assert call["url"] == "http://whisperx.local/asr"
        assert call["headers"]["Authorization"] == "Bearer test-key"
        assert call["data"]["language"] == "en"
    finally:
        if created_target and target_file.exists():
            target_file.unlink()


def test_invoke_whisperx_no_audio_attachments(monkeypatch):
    """Error when ticket has no audio attachments."""
    _webhook_helpers(monkeypatch)

    async def fake_get_ticket(tid):
        return {"id": tid, "subject": "No audio"}

    async def fake_list_attachments(tid):
        return [
            {
                "id": 2,
                "ticket_id": tid,
                "filename": "readme.pdf",
                "original_filename": "readme.pdf",
                "mime_type": "application/pdf",
                "file_size": 5000,
            }
        ]

    import app.repositories.tickets as tickets_repo_mod
    import app.repositories.ticket_attachments as attach_repo_mod
    monkeypatch.setattr(tickets_repo_mod, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(attach_repo_mod, "list_attachments", fake_list_attachments)

    settings = {"base_url": "http://whisperx.local", "api_key": "", "language": ""}
    payload = {"ticket_id": 5}

    result = asyncio.run(modules._invoke_whisperx(settings, payload))

    # Should fail gracefully with error status
    assert result["status"] == "failed"
    assert result["ticket_id"] == 5


def test_invoke_whisperx_missing_ticket_id():
    """Error when ticket_id is not provided."""
    settings = {"base_url": "http://whisperx.local"}
    payload = {}

    with pytest.raises(ValueError, match="ticket_id is required"):
        asyncio.run(modules._invoke_whisperx(settings, payload))


def test_invoke_whisperx_missing_base_url(monkeypatch):
    """Error when base_url is not configured."""
    _webhook_helpers(monkeypatch)

    settings = {"base_url": "", "api_key": ""}
    payload = {"ticket_id": 1}

    with pytest.raises(ValueError, match="base_url is not configured"):
        asyncio.run(modules._invoke_whisperx(settings, payload))


def test_invoke_whisperx_add_note_false(monkeypatch, tmp_path):
    """When add_note=false, transcription runs but no reply is created."""
    _webhook_helpers(monkeypatch)

    async def fake_get_ticket(tid):
        return {"id": tid}

    audio_file = tmp_path / "xyz.wav"
    audio_file.write_bytes(b"RIFF fake")

    async def fake_list_attachments(tid):
        return [
            {
                "id": 3,
                "ticket_id": tid,
                "filename": "xyz.wav",
                "original_filename": "message.wav",
                "mime_type": "audio/wav",
                "file_size": 512,
            }
        ]

    reply_created = {"called": False}

    async def fake_create_reply(**kw):
        reply_created["called"] = True
        return {"id": 99}

    async def fake_emit_event(tid, *, actor_type, trigger_automations):
        pass

    import app.repositories.tickets as tickets_repo_mod
    import app.repositories.ticket_attachments as attach_repo_mod
    monkeypatch.setattr(tickets_repo_mod, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(attach_repo_mod, "list_attachments", fake_list_attachments)
    monkeypatch.setattr(tickets_repo_mod, "create_reply", fake_create_reply)
    monkeypatch.setattr(modules.tickets_service, "emit_ticket_updated_event", fake_emit_event)

    whisperx_response = _FakeResponse({"text": "Transcribed text"})
    client_factory = _AsyncClientFactory(whisperx_response)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    # Place file
    upload_dir = Path(modules.__file__).parent.parent / "static" / "uploads" / "tickets"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_file = upload_dir / "xyz.wav"
    created_target = False
    if not target_file.exists():
        target_file.write_bytes(b"RIFF fake")
        created_target = True

    try:
        settings = {"base_url": "http://whisperx.local", "api_key": "", "language": ""}
        payload = {"ticket_id": 7, "add_note": False}

        result = asyncio.run(modules._invoke_whisperx(settings, payload))

        assert result["status"] == "succeeded"
        assert result["transcription_count"] == 1
        assert result["reply_id"] is None
        assert reply_created["called"] is False
    finally:
        if created_target and target_file.exists():
            target_file.unlink()


def test_invoke_whisperx_ticket_id_from_context(monkeypatch):
    """ticket_id can be resolved from context.ticket.id."""
    _webhook_helpers(monkeypatch)

    async def fake_get_ticket(tid):
        return {"id": tid}

    async def fake_list_attachments(tid):
        return []  # no attachments → will error

    import app.repositories.tickets as tickets_repo_mod
    import app.repositories.ticket_attachments as attach_repo_mod
    monkeypatch.setattr(tickets_repo_mod, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(attach_repo_mod, "list_attachments", fake_list_attachments)

    settings = {"base_url": "http://whisperx.local", "api_key": ""}
    payload = {"context": {"ticket": {"id": 42}}}

    result = asyncio.run(modules._invoke_whisperx(settings, payload))

    # Fails because no audio attachments, but ticket_id was resolved correctly
    assert result["ticket_id"] == 42
    assert result["status"] == "failed"


def test_is_audio_attachment_by_mime():
    """_is_audio_attachment matches common audio MIME types."""
    assert modules._is_audio_attachment({"mime_type": "audio/wav", "original_filename": "f.wav"})
    assert modules._is_audio_attachment({"mime_type": "audio/mpeg", "original_filename": "f.mp3"})
    assert modules._is_audio_attachment({"mime_type": "audio/ogg", "original_filename": "f.ogg"})
    assert not modules._is_audio_attachment({"mime_type": "application/pdf", "original_filename": "f.pdf"})
    assert not modules._is_audio_attachment({"mime_type": "image/png", "original_filename": "f.png"})


def test_is_audio_attachment_by_extension():
    """_is_audio_attachment falls back to file extension."""
    assert modules._is_audio_attachment({"mime_type": "", "original_filename": "voicemail.wav"})
    assert modules._is_audio_attachment({"mime_type": None, "original_filename": "recording.mp3"})
    assert modules._is_audio_attachment({"mime_type": "application/octet-stream", "original_filename": "call.flac"})
    assert not modules._is_audio_attachment({"mime_type": "", "original_filename": "document.txt"})
