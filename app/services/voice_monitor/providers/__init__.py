"""Provider adapter contract and safe value objects."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


class CallState(str, Enum):
    RINGING = "ringing"
    ANSWERED = "answered"
    COMPLETED = "completed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    FAILED = "failed"


@dataclass(frozen=True)
class OriginatedCall:
    call_id: str
    state: CallState = CallState.RINGING


@dataclass(frozen=True)
class MediaArtifact:
    reference: str
    content_type: str | None = None


class VoiceMonitorProvider(Protocol):
    """Provider-neutral call operations.

    Implementations load credentials from encrypted module settings or the
    environment. Credentials and raw callback bodies must never be returned.
    """

    async def originate(self, destination: str, *, idempotency_key: str) -> OriginatedCall: ...
    def map_callback(self, payload: Mapping[str, object]) -> CallState: ...
    async def status(self, call_id: str) -> CallState: ...
    async def hangup(self, call_id: str) -> None: ...
    async def retrieve_media(self, call_id: str) -> MediaArtifact | None: ...
