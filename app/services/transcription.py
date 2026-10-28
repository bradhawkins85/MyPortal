"""Shared, privacy-conscious clients for speech transcription services.

Two speech-to-text engines are supported:

* **WhisperX** (``whisperx``) – the ``whisper-asr-webservice`` style API which
  accepts ``POST /asr`` with an ``audio_file`` upload and options as URL query
  parameters.
* **whisper.cpp** (``whispercpp``) – the ``whisper-server`` HTTP example which
  accepts ``POST /inference`` (or the full URL configured in
  ``WHISPERCPP_BASE_URL``) with a ``file`` upload and options as multipart
  form fields.

Both return JSON with a ``text`` key and (optionally) timed ``segments``, so
callers share a single response parser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ENGINE_WHISPERX = "whisperx"
ENGINE_WHISPERCPP = "whispercpp"
TRANSCRIPTION_ENGINES: tuple[str, ...] = (ENGINE_WHISPERX, ENGINE_WHISPERCPP)

ENGINE_LABELS: dict[str, str] = {
    ENGINE_WHISPERX: "WhisperX",
    ENGINE_WHISPERCPP: "whisper.cpp",
}

_ENV_PREFIXES: dict[str, str] = {
    ENGINE_WHISPERX: "WHISPERX",
    ENGINE_WHISPERCPP: "WHISPERCPP",
}


class TranscriptionUnavailable(RuntimeError):
    """The configured transcription service cannot be used."""


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AsrRequest:
    """Engine-specific HTTP request shape for a single transcription call."""

    url: str
    file_field: str
    params: dict[str, str] = field(default_factory=dict)
    data: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


def _env_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def normalise_engine(value: Any) -> str:
    """Return a supported engine identifier, defaulting to WhisperX."""

    candidate = str(value or "").strip().lower().replace(".", "").replace("-", "").replace("_", "")
    return ENGINE_WHISPERCPP if candidate == ENGINE_WHISPERCPP else ENGINE_WHISPERX


def engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(normalise_engine(engine), ENGINE_LABELS[ENGINE_WHISPERX])


def resolve_engine_from_environment() -> str:
    """Choose the transcription engine for env-configured transcription.

    ``TRANSCRIPTION_ENGINE`` selects an engine explicitly. When it is unset the
    first engine with a configured base URL wins (WhisperX before whisper.cpp)
    so existing WhisperX deployments keep their behaviour.
    """

    explicit = os.getenv("TRANSCRIPTION_ENGINE", "").strip()
    if explicit:
        return normalise_engine(explicit)
    for engine in TRANSCRIPTION_ENGINES:
        if os.getenv(f"{_ENV_PREFIXES[engine]}_BASE_URL", "").strip():
            return engine
    return ENGINE_WHISPERX


def _whispercpp_url(base: str) -> str:
    """Use a configured URL with a path as-is; otherwise append ``/inference``.

    Operators can point at whatever route their whisper.cpp server exposes
    (e.g. ``/v1/audio/transcriptions``) without a code change.
    """

    if urlsplit(base).path.strip("/"):
        return base
    return f"{base}/inference"


def build_asr_request(
    engine: str, base_url: str, *, api_key: str = "", language: str = ""
) -> AsrRequest:
    """Build the request URL, upload field and options for *engine*."""

    base = (base_url or "").strip().rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if normalise_engine(engine) == ENGINE_WHISPERCPP:
        # whisper-server reads options from multipart form fields;
        # ``verbose_json`` includes the timed segments used for stereo merging.
        data = {"response_format": "verbose_json"}
        if language:
            data["language"] = language
        return AsrRequest(url=_whispercpp_url(base), file_field="file", data=data, headers=headers)
    # WhisperX /asr uses FastAPI Query(...) parameters, so options MUST be URL
    # query parameters; form fields are silently ignored.
    params = {"output": "json"}
    if language:
        params["language"] = language
    return AsrRequest(url=f"{base}/asr", file_field="audio_file", params=params, headers=headers)


@dataclass(frozen=True)
class TranscriptionSettings:
    base_url: str
    api_key: str = ""
    language: str = ""
    stereo_split: bool = False
    engine: str = ENGINE_WHISPERX

    @classmethod
    def from_environment(cls, engine: str | None = None) -> "TranscriptionSettings":
        selected = normalise_engine(engine) if engine else resolve_engine_from_environment()
        prefix = _ENV_PREFIXES[selected]
        return cls(
            base_url=os.getenv(f"{prefix}_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv(f"{prefix}_API_KEY", "").strip(),
            language=os.getenv(f"{prefix}_LANGUAGE", "").strip(),
            stereo_split=_env_enabled(os.getenv(f"{prefix}_STEREO_SPLIT")),
            engine=selected,
        )



class TranscriptionClient:
    """Small reusable ASR client which never logs audio or transcripts."""

    def __init__(self, settings: TranscriptionSettings | None = None, *, timeout: float = 300.0) -> None:
        self.settings = settings or TranscriptionSettings.from_environment()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.settings.base_url)

    async def transcribe(self, audio_path: str | Path, *, filename: str | None = None) -> TranscriptionResult:
        label = engine_label(self.settings.engine)
        if not self.available:
            raise TranscriptionUnavailable(f"{label} endpoint is not configured")
        path = Path(audio_path)
        if not path.is_file():
            raise TranscriptionUnavailable("audio is unavailable")
        request = build_asr_request(
            self.settings.engine, self.settings.base_url,
            api_key=self.settings.api_key, language=self.settings.language,
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                with path.open("rb") as audio:
                    response = await client.post(
                        request.url, params=request.params, data=request.data, headers=request.headers,
                        files={request.file_field: (filename or path.name, audio, "audio/wav")},
                    )
                    response.raise_for_status()
        except (OSError, httpx.HTTPError) as exc:
            # Do not include response bodies, URLs containing credentials, or file paths.
            raise TranscriptionUnavailable(f"{label} request failed ({type(exc).__name__})") from exc
        try:
            payload = response.json()
        except ValueError:
            text, segments = response.text.strip(), ()
        else:
            if not isinstance(payload, dict):
                raise TranscriptionUnavailable(f"{label} returned an invalid response")
            text = str(payload.get("text") or "").strip()
            segments = tuple(item for item in (payload.get("segments") or ()) if isinstance(item, dict))
        if not text:
            raise TranscriptionUnavailable(f"{label} returned an empty transcription")
        return TranscriptionResult(text=text, segments=segments)

