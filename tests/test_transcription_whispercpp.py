"""Tests for whisper.cpp support alongside WhisperX transcription."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import modules
from app.services import transcription
from app.services.transcription import (
    ENGINE_WHISPERCPP,
    ENGINE_WHISPERX,
    TranscriptionClient,
    TranscriptionSettings,
    build_asr_request,
    normalise_engine,
    resolve_engine_from_environment,
)

from tests.test_modules_whisperx import _AsyncClientFactory, _FakeResponse

_ENV_KEYS = (
    "TRANSCRIPTION_ENGINE",
    "WHISPERX_BASE_URL", "WHISPERX_API_KEY", "WHISPERX_LANGUAGE", "WHISPERX_STEREO_SPLIT",
    "WHISPERCPP_BASE_URL", "WHISPERCPP_API_KEY", "WHISPERCPP_LANGUAGE", "WHISPERCPP_STEREO_SPLIT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("whispercpp", ENGINE_WHISPERCPP),
        ("whisper.cpp", ENGINE_WHISPERCPP),
        ("Whisper-CPP", ENGINE_WHISPERCPP),
        ("whisperx", ENGINE_WHISPERX),
        ("", ENGINE_WHISPERX),
        (None, ENGINE_WHISPERX),
        ("unknown", ENGINE_WHISPERX),
    ],
)
def test_normalise_engine(value, expected):
    assert normalise_engine(value) == expected


def test_build_asr_request_whisperx_uses_query_params():
    request = build_asr_request("whisperx", "http://asr.local/", api_key="k", language="en")
    assert request.url == "http://asr.local/asr"
    assert request.file_field == "audio_file"
    assert request.params == {"output": "json", "language": "en"}
    assert request.data == {}
    assert request.headers == {"Authorization": "Bearer k"}


def test_build_asr_request_whispercpp_uses_form_fields():
    request = build_asr_request("whispercpp", "http://cpp.local/", language="en")
    assert request.url == "http://cpp.local/inference"
    assert request.file_field == "file"
    assert request.params == {}
    assert request.data == {"response_format": "verbose_json", "language": "en"}
    assert request.headers == {}


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://cpp.local", "http://cpp.local/inference"),
        ("http://cpp.local/", "http://cpp.local/inference"),
        ("http://cpp.local/inference", "http://cpp.local/inference"),
        ("http://cpp.local/v1/audio/transcriptions", "http://cpp.local/v1/audio/transcriptions"),
    ],
)
def test_build_asr_request_whispercpp_uses_full_url_when_path_given(base_url, expected):
    assert build_asr_request("whispercpp", base_url).url == expected


def test_engine_resolution_prefers_explicit_then_configured(monkeypatch):
    assert resolve_engine_from_environment() == ENGINE_WHISPERX
    monkeypatch.setenv("WHISPERCPP_BASE_URL", "http://cpp.local")
    assert resolve_engine_from_environment() == ENGINE_WHISPERCPP
    monkeypatch.setenv("WHISPERX_BASE_URL", "http://wx.local")
    assert resolve_engine_from_environment() == ENGINE_WHISPERX
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "whispercpp")
    assert resolve_engine_from_environment() == ENGINE_WHISPERCPP


def test_settings_from_environment_reads_whispercpp_keys(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "whispercpp")
    monkeypatch.setenv("WHISPERX_BASE_URL", "http://wx.local")
    monkeypatch.setenv("WHISPERCPP_BASE_URL", "http://cpp.local/")
    monkeypatch.setenv("WHISPERCPP_LANGUAGE", "de")
    monkeypatch.setenv("WHISPERCPP_STEREO_SPLIT", "true")
    settings = TranscriptionSettings.from_environment()
    assert settings == TranscriptionSettings(
        base_url="http://cpp.local", api_key="", language="de",
        stereo_split=True, engine=ENGINE_WHISPERCPP,
    )


def test_module_settings_switch_to_whispercpp_from_env(monkeypatch):
    """An env-selected engine must not reuse the other engine's stored credentials."""
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "whispercpp")
    monkeypatch.setenv("WHISPERCPP_BASE_URL", "http://cpp.local/")

    settings = modules._coerce_settings(
        "whisperx",
        {
            "base_url": "http://stored-whisperx.local",
            "api_key": "stored-whisperx-key",
            "language": "en",
            "stereo_split": False,
        },
    )

    assert settings == {
        "engine": "whispercpp",
        "base_url": "http://cpp.local",
        "api_key": "",
        "language": "en",
        "stereo_split": False,
    }


def test_module_settings_keep_stored_whispercpp_engine_without_env():
    settings = modules._coerce_settings(
        "whisperx",
        {"engine": "whispercpp", "base_url": "http://cpp.local", "language": "fr"},
    )
    assert settings["engine"] == "whispercpp"
    assert settings["base_url"] == "http://cpp.local"
    assert settings["language"] == "fr"


def test_transcription_client_posts_to_whispercpp(monkeypatch, tmp_path):
    audio = tmp_path / "call.wav"
    audio.write_bytes(b"RIFF fake")
    factory = _AsyncClientFactory(_FakeResponse({"text": " Hello from cpp. ", "segments": []}))
    monkeypatch.setattr(transcription.httpx, "AsyncClient", lambda *a, **kw: factory)

    client = TranscriptionClient(
        TranscriptionSettings(base_url="http://cpp.local", language="en", engine=ENGINE_WHISPERCPP)
    )
    result = asyncio.run(client.transcribe(audio))

    assert result.text == "Hello from cpp."
    call = factory.calls[0]
    assert call["url"] == "http://cpp.local/inference"
    assert set(call["files"]) == {"file"}
    assert call["data"] == {"response_format": "verbose_json", "language": "en"}


def test_invoke_ticket_transcription_with_whispercpp(monkeypatch, tmp_path):
    from app.services import webhook_monitor

    async def fake_record(event_id, **kwargs):
        return {"id": event_id, "status": "succeeded" if "status" not in kwargs else kwargs["status"]}

    monkeypatch.setattr(webhook_monitor, "create_manual_event", AsyncMock(return_value={"id": 3}))
    monkeypatch.setattr(modules, "_record_success", fake_record)
    monkeypatch.setattr(modules, "_record_failure", fake_record)
    audio_file = tmp_path / "vm.wav"
    audio_file.write_bytes(b"RIFF fake wav content")

    async def fake_get_ticket(tid):
        return {"id": tid}

    async def fake_list_attachments(tid, *, access_levels=None):
        return [{
            "id": 1, "ticket_id": tid, "filename": "vm.wav",
            "original_filename": "voicemail.wav", "mime_type": "audio/wav",
        }]

    import app.repositories.tickets as tickets_repo_mod
    import app.repositories.ticket_attachments as attach_repo_mod
    import app.services.ticket_attachments as attach_service_mod

    monkeypatch.setattr(tickets_repo_mod, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(attach_repo_mod, "list_attachments", fake_list_attachments)
    monkeypatch.setattr(attach_service_mod, "get_attachment_file_path", lambda name: audio_file)

    factory = _AsyncClientFactory(_FakeResponse({"text": "Please call me back."}))
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: factory)

    settings = {"engine": "whispercpp", "base_url": "http://cpp.local", "language": "en"}
    result = asyncio.run(modules._invoke_whisperx(settings, {"ticket_id": 7, "add_note": False}))

    assert result["status"] == "succeeded", result
    assert result["transcription_count"] == 1
    call = factory.calls[0]
    assert call["url"] == "http://cpp.local/inference"
    assert set(call["files"]) == {"file"}
    assert call["data"] == {"response_format": "verbose_json", "language": "en"}
    assert call["params"] == {}


def test_call_recording_transcription_uses_whispercpp_env(monkeypatch, tmp_path):
    from app.repositories import call_recordings as call_recordings_repo
    from app.services import call_recordings as service

    audio = tmp_path / "call.wav"
    audio.write_bytes(b"fake audio data")
    recording = {
        "id": 5, "file_path": str(audio), "file_name": "call.wav",
        "transcription": None, "transcription_status": "pending",
    }
    monkeypatch.setattr(service, "_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "whispercpp")
    monkeypatch.setenv("WHISPERCPP_BASE_URL", "http://cpp.local/")
    monkeypatch.setenv("WHISPERCPP_LANGUAGE", "en")

    async def fake_get_module(slug, *args, **kwargs):
        return {"slug": slug, "settings": {"phone_system_type": "generic"}}

    update = AsyncMock(return_value={**recording, "transcription": "Hi there.", "transcription_status": "completed"})
    monkeypatch.setattr(call_recordings_repo, "get_call_recording_by_id", AsyncMock(return_value=recording))
    monkeypatch.setattr(call_recordings_repo, "update_call_recording", update)
    monkeypatch.setattr(service.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(service.ai_consent, "is_ai_allowed_for_call", AsyncMock(return_value=True))
    gate = AsyncMock(return_value={"enabled": True})
    monkeypatch.setattr(service, "require_module_enabled", gate)
    monkeypatch.setattr(service.webhook_monitor, "create_manual_event", AsyncMock(return_value={"id": 1}))
    monkeypatch.setattr(service.webhook_monitor, "record_manual_success", AsyncMock())

    response = MagicMock(status_code=200, content=b"{}", text='{"text": "Hi there."}')
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"text": "Hi there."}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda *a, **kw: client)

    result = asyncio.run(service.transcribe_recording(5))

    assert result["transcription"] == "Hi there."
    gate.assert_awaited_once_with("whisperx")
    post = client.post.call_args
    assert post.args[0] == "http://cpp.local/inference"
    assert set(post.kwargs["files"]) == {"file"}
    assert post.kwargs["data"] == {"response_format": "verbose_json", "language": "en"}
    assert post.kwargs["params"] == {}
