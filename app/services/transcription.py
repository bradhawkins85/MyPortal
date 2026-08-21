"""Shared, privacy-conscious clients for speech transcription services."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import httpx


class TranscriptionUnavailable(RuntimeError):
    """The configured transcription service cannot be used."""


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class WhisperXSettings:
    base_url: str
    api_key: str = ""
    language: str = ""
    stereo_split: bool = False

    @classmethod
    def from_environment(cls) -> "WhisperXSettings":
        enabled = lambda value: (value or "").strip().lower() not in {"", "0", "false", "no", "off"}
        return cls(
            base_url=os.getenv("WHISPERX_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv("WHISPERX_API_KEY", "").strip(),
            language=os.getenv("WHISPERX_LANGUAGE", "").strip(),
            stereo_split=enabled(os.getenv("WHISPERX_STEREO_SPLIT")),
        )


class WhisperXClient:
    """Small reusable WhisperX client which never logs audio or transcripts."""

    def __init__(self, settings: WhisperXSettings | None = None, *, timeout: float = 300.0) -> None:
        self.settings = settings or WhisperXSettings.from_environment()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.settings.base_url)

    async def transcribe(self, audio_path: str | Path, *, filename: str | None = None) -> TranscriptionResult:
        if not self.available:
            raise TranscriptionUnavailable("WhisperX endpoint is not configured")
        path = Path(audio_path)
        if not path.is_file():
            raise TranscriptionUnavailable("audio is unavailable")
        headers = {"Authorization": f"Bearer {self.settings.api_key}"} if self.settings.api_key else {}
        params = {"output": "json"}
        if self.settings.language:
            params["language"] = self.settings.language
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                with path.open("rb") as audio:
                    response = await client.post(
                        f"{self.settings.base_url}/asr", params=params, headers=headers,
                        files={"audio_file": (filename or path.name, audio, "audio/wav")},
                    )
                    response.raise_for_status()
        except (OSError, httpx.HTTPError) as exc:
            # Do not include response bodies, URLs containing credentials, or file paths.
            raise TranscriptionUnavailable(f"WhisperX request failed ({type(exc).__name__})") from exc
        try:
            payload = response.json()
        except ValueError:
            text, segments = response.text.strip(), ()
        else:
            if not isinstance(payload, dict):
                raise TranscriptionUnavailable("WhisperX returned an invalid response")
            text = str(payload.get("text") or "").strip()
            segments = tuple(item for item in (payload.get("segments") or ()) if isinstance(item, dict))
        if not text:
            raise TranscriptionUnavailable("WhisperX returned an empty transcription")
        return TranscriptionResult(text=text, segments=segments)
