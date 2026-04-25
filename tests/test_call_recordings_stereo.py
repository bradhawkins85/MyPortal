"""Tests for WhisperX stereo channel split and transcription formatting."""
from __future__ import annotations

import array
import struct
import wave
from pathlib import Path

import pytest

from app.services.call_recordings import (
    _build_stereo_transcription,
    _fmt_time,
    _parse_whisperx_response,
    _split_stereo_wav,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stereo_wav(path: Path, n_frames: int = 100, sample_rate: int = 8000) -> None:
    """Write a minimal 2-channel 16-bit PCM WAV file to *path*."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # Alternate left (100) and right (200) samples so we can verify split
        samples = array.array("h")
        for _ in range(n_frames):
            samples.append(100)   # left  / callee
            samples.append(200)   # right / caller
        w.writeframes(samples.tobytes())


def _make_mono_wav(path: Path, n_frames: int = 50, sample_rate: int = 8000) -> None:
    """Write a minimal 1-channel 16-bit PCM WAV file to *path*."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = array.array("h", [150] * n_frames)
        w.writeframes(samples.tobytes())


class _FakeResponse:
    """Minimal httpx.Response stand-in for _parse_whisperx_response tests."""

    def __init__(self, body, content_type: str = "application/json"):
        import json as _json
        self._body = body
        self.content_type = content_type
        if isinstance(body, dict):
            self._text = _json.dumps(body)
        else:
            self._text = str(body)
        self.headers = {"content-type": content_type}

    @property
    def text(self):
        return self._text

    def json(self):
        import json as _json
        if isinstance(self._body, dict):
            return self._body
        return _json.loads(self._text)


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------

def test_fmt_time_seconds():
    assert _fmt_time(0) == "00:00"
    assert _fmt_time(59) == "00:59"
    assert _fmt_time(60) == "01:00"
    assert _fmt_time(90) == "01:30"
    assert _fmt_time(3661) == "61:01"


def test_fmt_time_negative_clamps_to_zero():
    assert _fmt_time(-5) == "00:00"


def test_fmt_time_float_truncates():
    assert _fmt_time(1.9) == "00:01"


# ---------------------------------------------------------------------------
# _parse_whisperx_response
# ---------------------------------------------------------------------------

def test_parse_whisperx_json_with_text_only():
    resp = _FakeResponse({"text": "Hello world"})
    text, segs = _parse_whisperx_response(resp)
    assert text == "Hello world"
    assert segs == []


def test_parse_whisperx_json_with_segments():
    body = {
        "text": "Hello world",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.0, "end": 2.0, "text": "world"},
        ],
    }
    resp = _FakeResponse(body)
    text, segs = _parse_whisperx_response(resp)
    assert text == "Hello world"
    assert len(segs) == 2
    assert segs[0]["text"] == "Hello"


def test_parse_whisperx_plain_text_fallback():
    resp = _FakeResponse("plain text response", content_type="text/plain")
    text, segs = _parse_whisperx_response(resp)
    assert text == "plain text response"
    assert segs == []


# ---------------------------------------------------------------------------
# _build_stereo_transcription
# ---------------------------------------------------------------------------

def test_build_stereo_transcription_section_format():
    result = _build_stereo_transcription(
        caller_text="Hello, this is the caller.",
        caller_segments=[],
        callee_text="This is the callee speaking.",
        callee_segments=[],
    )
    assert "**Caller:**" in result
    assert "Hello, this is the caller." in result
    assert "**Callee:**" in result
    assert "This is the callee speaking." in result


def test_build_stereo_transcription_only_caller():
    result = _build_stereo_transcription(
        caller_text="Only caller text.",
        caller_segments=[],
        callee_text="",
        callee_segments=[],
    )
    assert "**Caller:**" in result
    assert "**Callee:**" not in result


def test_build_stereo_transcription_only_callee():
    result = _build_stereo_transcription(
        caller_text="",
        caller_segments=[],
        callee_text="Only callee text.",
        callee_segments=[],
    )
    assert "**Callee:**" in result
    assert "**Caller:**" not in result


def test_build_stereo_transcription_interleaved_by_segments():
    caller_segs = [{"start": 2.0, "text": "I am the caller."}]
    callee_segs = [{"start": 0.5, "text": "I am the callee."}]
    result = _build_stereo_transcription(
        caller_text="I am the caller.",
        caller_segments=caller_segs,
        callee_text="I am the callee.",
        callee_segments=callee_segs,
    )
    # Callee segment at 0.5s should come first
    callee_pos = result.index("Callee")
    caller_pos = result.index("Caller")
    assert callee_pos < caller_pos
    assert "[00:00]" in result  # 0.5s → 00:00
    assert "[00:02]" in result  # 2.0s → 00:02


def test_build_stereo_transcription_empty_both():
    result = _build_stereo_transcription("", [], "", [])
    assert result == ""


# ---------------------------------------------------------------------------
# _split_stereo_wav
# ---------------------------------------------------------------------------

def test_split_stereo_wav_produces_two_mono_files(tmp_path):
    src = tmp_path / "stereo.wav"
    _make_stereo_wav(src, n_frames=100)

    result = _split_stereo_wav(src)
    assert result is not None
    callee_path, caller_path = result

    try:
        # Both files should exist
        assert callee_path.exists()
        assert caller_path.exists()

        # Each should be a valid mono WAV
        with wave.open(str(callee_path), "rb") as w:
            assert w.getnchannels() == 1
            callee_samples = array.array("h", w.readframes(w.getnframes()))

        with wave.open(str(caller_path), "rb") as w:
            assert w.getnchannels() == 1
            caller_samples = array.array("h", w.readframes(w.getnframes()))

        # Callee = left channel (value 100), Caller = right channel (value 200)
        assert all(s == 100 for s in callee_samples)
        assert all(s == 200 for s in caller_samples)
    finally:
        callee_path.unlink(missing_ok=True)
        caller_path.unlink(missing_ok=True)


def test_split_stereo_wav_returns_none_for_mono(tmp_path):
    src = tmp_path / "mono.wav"
    _make_mono_wav(src)
    assert _split_stereo_wav(src) is None


def test_split_stereo_wav_returns_none_for_non_wav(tmp_path):
    src = tmp_path / "audio.mp3"
    src.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00")  # fake MP3 header
    assert _split_stereo_wav(src) is None


def test_split_stereo_wav_returns_none_for_empty_file(tmp_path):
    src = tmp_path / "empty.wav"
    src.write_bytes(b"")
    assert _split_stereo_wav(src) is None


def test_split_stereo_wav_returns_none_for_missing_file(tmp_path):
    src = tmp_path / "nonexistent.wav"
    assert _split_stereo_wav(src) is None


def test_split_stereo_wav_preserves_frame_rate(tmp_path):
    src = tmp_path / "stereo.wav"
    _make_stereo_wav(src, sample_rate=16000)

    result = _split_stereo_wav(src)
    assert result is not None
    callee_path, caller_path = result

    try:
        for path in (callee_path, caller_path):
            with wave.open(str(path), "rb") as w:
                assert w.getframerate() == 16000
    finally:
        callee_path.unlink(missing_ok=True)
        caller_path.unlink(missing_ok=True)
