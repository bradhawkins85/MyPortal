"""Tests for Grandstream UCM phone-system call recording processing."""
from __future__ import annotations

import array
import asyncio
import wave
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import call_recordings as service
from app.services import modules as modules_service


GRANDSTREAM_CSV_HEADER = (
    "file_name,caller_num,callee_num,create_time,file_size,duration,path_type,"
    "other_num,feature_type,file_date,location_status,gdms_uuid,caller_name,"
    "callee_name,other_name,new_caller_num,file_key,file_key_check"
)


def _write_grandstream_layout(base_path, period: str, rows: list[dict[str, str]]):
    """Create the Grandstream UCM directory layout under ``base_path``."""
    folder = base_path / period
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        # Create the audio file that the CSV row references.
        audio = folder / row["file_name"]
        audio.write_bytes(b"fake audio")

    csv_path = base_path / f".rd_files_netdisk_{period}.csv"
    lines = [GRANDSTREAM_CSV_HEADER]
    for row in rows:
        ordered = [
            row.get("file_name", ""),
            row.get("caller_num", ""),
            row.get("callee_num", ""),
            row.get("create_time", ""),
            row.get("file_size", ""),
            row.get("duration", ""),
            row.get("path_type", ""),
            row.get("other_num", ""),
            row.get("feature_type", ""),
            row.get("file_date", ""),
            row.get("location_status", ""),
            row.get("gdms_uuid", ""),
            row.get("caller_name", ""),
            row.get("callee_name", ""),
            row.get("other_name", ""),
            row.get("new_caller_num", ""),
            row.get("file_key", ""),
            row.get("file_key_check", ""),
        ]
        lines.append(",".join(ordered))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path


def test_phone_system_type_constants_exposed():
    """The supported phone-system types should be exposed for the dropdown."""
    assert "generic" in modules_service.CALL_RECORDINGS_PHONE_SYSTEM_TYPES
    assert "grandstream-ucm" in modules_service.CALL_RECORDINGS_PHONE_SYSTEM_TYPES
    assert "3cx" in modules_service.CALL_RECORDINGS_PHONE_SYSTEM_TYPES


def test_normalize_phone_system_type_defaults_to_generic():
    assert service._normalize_phone_system_type(None) == "generic"
    assert service._normalize_phone_system_type("") == "generic"
    assert service._normalize_phone_system_type("unknown-system") == "generic"
    assert service._normalize_phone_system_type("Grandstream-UCM") == "grandstream-ucm"
    assert service._normalize_phone_system_type("3CX") == "3cx"


def test_call_recordings_module_default_includes_phone_system_type():
    """The default settings for call-recordings must include phone_system_type."""
    defaults = next(
        m for m in modules_service.DEFAULT_MODULES if m["slug"] == "call-recordings"
    )
    assert defaults["settings"]["phone_system_type"] == "generic"


def test_normalize_settings_coerces_invalid_phone_system_type():
    """Unknown phone system values should be coerced back to ``generic``."""
    normalised = modules_service._coerce_settings(
        "call-recordings",
        {"phone_system_type": "bogus", "recordings_path": "/tmp/foo"},
    )
    assert normalised["phone_system_type"] == "generic"
    assert normalised["recordings_path"] == "/tmp/foo"


def test_normalize_settings_accepts_grandstream_and_3cx():
    for value in ("grandstream-ucm", "3cx"):
        normalised = modules_service._coerce_settings(
            "call-recordings",
            {"phone_system_type": value, "recordings_path": "/tmp/x"},
        )
        assert normalised["phone_system_type"] == value


@pytest.mark.asyncio
async def test_grandstream_sync_creates_records_from_csv(tmp_path):
    """Grandstream UCM sync should create recordings from the CSV index."""
    from app.repositories import call_recordings as repo

    period = "2026-04"
    rows = [
        {
            "file_name": "auto-1714132800-from-61400000001-to-1001.wav",
            "caller_num": "61400000001",
            "callee_num": "1001",
            "create_time": "1714132800",  # 2024-04-26 12:00:00 UTC
            "duration": "125",
            "file_date": "2024-04-26",
            "new_caller_num": "+61400000001",
        },
        {
            "file_name": "auto-1714132900-from-1002-to-61400000002.wav",
            "caller_num": "1002",
            "callee_num": "61400000002",
            "create_time": "1714132900",
            "duration": "00:02:30",
            "file_date": "2024-04-26",
        },
    ]
    _write_grandstream_layout(tmp_path, period, rows)

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "force_update_call_recording", new_callable=AsyncMock) as mock_force_update, \
         patch.object(repo, "lookup_staff_by_phone", new_callable=AsyncMock) as mock_lookup:
        mock_get.return_value = None
        mock_create.return_value = {"id": 1}
        mock_lookup.return_value = None

        result = await service.sync_recordings_from_filesystem(
            str(tmp_path), phone_system_type="grandstream-ucm"
        )

    assert result["status"] == "ok"
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []
    assert mock_create.await_count == 2
    mock_force_update.assert_not_called()

    first_kwargs = mock_create.await_args_list[0].kwargs
    # Prefers ``new_caller_num`` when present.
    assert first_kwargs["phone_number"] == "+61400000001"
    assert first_kwargs["duration_seconds"] == 125
    assert first_kwargs["call_date"] == datetime(2024, 4, 26, 12, 0, tzinfo=timezone.utc)
    assert first_kwargs["transcription_status"] == "pending"
    assert first_kwargs["file_name"].endswith(".wav")

    second_kwargs = mock_create.await_args_list[1].kwargs
    # Falls back to caller_num when new_caller_num is not provided.
    assert second_kwargs["phone_number"] == "1002"
    # Duration like ``HH:MM:SS`` should be coerced to total seconds.
    assert second_kwargs["duration_seconds"] == 150


@pytest.mark.asyncio
async def test_grandstream_sync_skips_when_audio_missing(tmp_path):
    """Rows referencing missing audio files should be skipped, not errored."""
    from app.repositories import call_recordings as repo

    period = "2026-05"
    csv_path = tmp_path / f".rd_files_netdisk_{period}.csv"
    csv_path.write_text(
        GRANDSTREAM_CSV_HEADER
        + "\nmissing.wav,1001,2002,1714132800,1024,30,,,,,,,,,,,,\n",
        encoding="utf-8",
    )
    # Create an empty period folder so the directory exists.
    (tmp_path / period).mkdir()

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create:
        mock_get.return_value = None

        result = await service.sync_recordings_from_filesystem(
            str(tmp_path), phone_system_type="grandstream-ucm"
        )

    assert result["status"] == "ok"
    assert result["created"] == 0
    assert result["skipped"] == 1
    mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_grandstream_sync_no_csv_files_returns_warning(tmp_path):
    """If no Grandstream CSV files are found, sync should return a warning."""
    result = await service.sync_recordings_from_filesystem(
        str(tmp_path), phone_system_type="grandstream-ucm"
    )

    assert result["status"] == "ok"
    assert result["created"] == 0
    assert result["updated"] == 0
    assert any("Grandstream" in err for err in result["errors"])


@pytest.mark.asyncio
async def test_grandstream_sync_skips_existing_when_not_forced(tmp_path):
    """Existing recordings should be skipped when not running a force sync."""
    from app.repositories import call_recordings as repo

    period = "2026-04"
    rows = [
        {
            "file_name": "call.wav",
            "caller_num": "1234567890",
            "callee_num": "0987654321",
            "create_time": "1714132800",
            "duration": "60",
            "file_date": "2024-04-26",
        }
    ]
    _write_grandstream_layout(tmp_path, period, rows)

    existing = {
        "id": 99,
        "file_name": "call.wav",
        "phone_number": "1234567890",
        "call_date": datetime(2024, 4, 26, 12, 0),
        "duration_seconds": 60,
        "caller_staff_id": None,
    }

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "force_update_call_recording", new_callable=AsyncMock) as mock_update:
        mock_get.return_value = existing

        result = await service.sync_recordings_from_filesystem(
            str(tmp_path), phone_system_type="grandstream-ucm"
        )

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1
    mock_create.assert_not_called()
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_grandstream_force_sync_updates_existing(tmp_path):
    """Force sync should refresh metadata for existing Grandstream recordings."""
    from app.repositories import call_recordings as repo

    period = "2026-04"
    rows = [
        {
            "file_name": "call.wav",
            "caller_num": "1234567890",
            "callee_num": "0987654321",
            "create_time": "1714132800",
            "duration": "60",
            "file_date": "2024-04-26",
            "new_caller_num": "+1234567890",
        }
    ]
    _write_grandstream_layout(tmp_path, period, rows)

    existing = {
        "id": 99,
        "file_name": "call.wav",
        "phone_number": "old-number",
        "call_date": datetime(2020, 1, 1),
        "duration_seconds": 10,
        "caller_staff_id": None,
    }

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create, \
         patch.object(repo, "force_update_call_recording", new_callable=AsyncMock) as mock_update, \
         patch.object(repo, "lookup_staff_by_phone", new_callable=AsyncMock) as mock_lookup:
        mock_get.return_value = existing
        mock_lookup.return_value = None

        result = await service.force_sync_recordings_from_filesystem(
            str(tmp_path), phone_system_type="grandstream-ucm"
        )

    assert result["created"] == 0
    assert result["updated"] == 1
    mock_create.assert_not_called()
    mock_update.assert_awaited_once()
    update_kwargs = mock_update.await_args.kwargs
    assert update_kwargs["phone_number"] == "+1234567890"
    assert update_kwargs["duration_seconds"] == 60


@pytest.mark.asyncio
async def test_3cx_phone_system_uses_generic_processing(tmp_path):
    """The 3CX option should currently delegate to the generic discovery flow."""
    from app.repositories import call_recordings as repo

    audio = tmp_path / "call.wav"
    audio.write_bytes(b"fake")

    with patch.object(repo, "get_call_recording_by_file_path", new_callable=AsyncMock) as mock_get, \
         patch.object(repo, "create_call_recording", new_callable=AsyncMock) as mock_create:
        mock_get.return_value = None
        mock_create.return_value = {"id": 1}

        result = await service.sync_recordings_from_filesystem(
            str(tmp_path), phone_system_type="3cx"
        )

    assert result["status"] == "ok"
    assert result["created"] == 1
    assert mock_create.await_count == 1


@pytest.mark.asyncio
async def test_module_handler_passes_grandstream_phone_system_type():
    """``_validate_call_recordings`` should pass the configured phone system type."""
    with patch.object(
        modules_service.call_recordings_service,
        "sync_recordings_from_filesystem",
        new_callable=AsyncMock,
    ) as mock_sync:
        mock_sync.return_value = {
            "status": "ok",
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

        result = await modules_service._validate_call_recordings(
            settings={
                "recordings_path": "/data/recordings",
                "phone_system_type": "grandstream-ucm",
            },
            payload={},
        )

    mock_sync.assert_awaited_once_with(
        "/data/recordings", phone_system_type="grandstream-ucm"
    )
    assert result["phone_system_type"] == "grandstream-ucm"
    assert result["recordings_path"] == "/data/recordings"


# ---------------------------------------------------------------------------
# Transcription tests – Grandstream UCM auto stereo-split
# ---------------------------------------------------------------------------

def _make_stereo_wav(path: Path) -> None:
    """Write a minimal 2-channel PCM WAV to *path*."""
    samples = array.array("h", [100, -100] * 50)  # interleaved L/R
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(samples.tobytes())


@pytest.mark.asyncio
async def test_grandstream_ucm_transcription_auto_enables_stereo_split(tmp_path):
    """When phone_system_type is grandstream-ucm, transcribe_recording should
    automatically perform a stereo split and return a labelled Caller/Callee
    transcription even when the WhisperX module does NOT have stereo_split=True.
    """
    from app.repositories import call_recordings as repo
    from app.repositories import integration_modules as modules_repo_mod

    stereo_wav = tmp_path / "call.wav"
    _make_stereo_wav(stereo_wav)

    recording = {
        "id": 1,
        "file_path": str(stereo_wav),
        "file_name": stereo_wav.name,
        "transcription": None,
        "transcription_status": "queued",
    }

    whisperx_module = {
        "slug": "whisperx",
        "enabled": True,
        "settings": {
            "base_url": "http://whisperx.local",
            "api_key": "",
            "language": "",
            # stereo_split is intentionally absent / False
        },
    }

    call_recordings_module = {
        "slug": "call-recordings",
        "enabled": True,
        "settings": {
            "recordings_path": str(tmp_path),
            "phone_system_type": "grandstream-ucm",
        },
    }

    post_call_count = 0

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, text_value: str):
            self._text = text_value
            self.content = text_value.encode()

        @property
        def text(self):
            return self._text

        def json(self):
            return {"text": self._text}

        def raise_for_status(self):
            pass

    _responses = [
        _FakeResponse("Caller says hello."),
        _FakeResponse("Callee says hi."),
    ]

    class _MockClient:
        def __init__(self):
            self._responses = iter(_responses)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, files=None, data=None, headers=None):
            nonlocal post_call_count
            post_call_count += 1
            return next(self._responses)

    updated_recording: dict = dict(recording)

    async def fake_get_module(slug: str):
        if slug == "whisperx":
            return whisperx_module
        if slug == "call-recordings":
            return call_recordings_module
        return None

    async def fake_get_by_id(rid):
        return updated_recording

    async def fake_update(rid, *, transcription=None, transcription_status=None, **kw):
        if transcription is not None:
            updated_recording["transcription"] = transcription
        if transcription_status is not None:
            updated_recording["transcription_status"] = transcription_status
        return updated_recording

    with patch.object(modules_repo_mod, "get_module", side_effect=fake_get_module), \
         patch.object(repo, "get_call_recording_by_id", side_effect=fake_get_by_id), \
         patch.object(repo, "update_call_recording", side_effect=fake_update), \
         patch("app.services.call_recordings.webhook_monitor.create_manual_event", new_callable=AsyncMock) as mock_wh_create, \
         patch("app.services.call_recordings.webhook_monitor.record_manual_success", new_callable=AsyncMock), \
         patch("app.services.call_recordings.httpx.AsyncClient", lambda *a, **kw: _MockClient()):
        mock_wh_create.return_value = {"id": 42}

        result = await service.transcribe_recording(1, force=False)

    # Two HTTP calls must have been made (one per stereo channel)
    assert post_call_count == 2, f"Expected 2 WhisperX calls, got {post_call_count}"

    transcription = result.get("transcription") or updated_recording.get("transcription") or ""
    assert "**Caller:**" in transcription or "Caller" in transcription, (
        f"Caller label missing from transcription: {transcription!r}"
    )
    assert "**Callee:**" in transcription or "Callee" in transcription, (
        f"Callee label missing from transcription: {transcription!r}"
    )
