"""Call-scoped RTP reconstruction and private Voice Monitor media storage.

This module intentionally has no packet-capture implementation.  Packets must
come from the configured provider or a per-call RTP socket bound to the ports
negotiated for that call; broad host/interface capture is not accepted.
"""
from __future__ import annotations

import audioop
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import struct
from typing import Iterable, Sequence
import wave

from cryptography.fernet import Fernet, InvalidToken


class MediaValidationError(ValueError):
    pass


class IncompleteMediaError(MediaValidationError):
    def __init__(self, message: str, *, received: int, lost: int) -> None:
        super().__init__(message)
        self.received, self.lost = received, lost


@dataclass(frozen=True)
class CaptureScope:
    call_id: str
    ports: frozenset[int]
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.ports:
            raise MediaValidationError("capture requires a call identifier and negotiated RTP ports")
        if any(port < 1 or port > 65535 for port in self.ports):
            raise MediaValidationError("invalid RTP port")
        if self.ended_at <= self.started_at:
            raise MediaValidationError("invalid call capture time window")


@dataclass(frozen=True)
class RTPPacket:
    sequence: int
    timestamp: int
    payload_type: int
    payload: bytes
    ssrc: int
    marker: bool = False

    @classmethod
    def parse(cls, packet: bytes) -> "RTPPacket":
        if len(packet) < 12:
            raise MediaValidationError("truncated RTP header")
        first, second, sequence, timestamp, ssrc = struct.unpack("!BBHII", packet[:12])
        if first >> 6 != 2:
            raise MediaValidationError("unsupported RTP version")
        csrc_count, extension, padding = first & 15, bool(first & 16), bool(first & 32)
        offset = 12 + csrc_count * 4
        if len(packet) < offset:
            raise MediaValidationError("truncated RTP CSRC list")
        if extension:
            if len(packet) < offset + 4:
                raise MediaValidationError("truncated RTP extension")
            words = struct.unpack("!H", packet[offset + 2:offset + 4])[0]
            offset += 4 + words * 4
        if offset >= len(packet):
            raise MediaValidationError("RTP packet has no payload")
        end = len(packet)
        if padding:
            count = packet[-1]
            if count == 0 or count > end - offset:
                raise MediaValidationError("invalid RTP padding")
            end -= count
        return cls(sequence, timestamp, second & 127, packet[offset:end], ssrc, bool(second & 128))


@dataclass(frozen=True)
class MediaResult:
    pcm: bytes
    codec: str
    sample_rate: int
    channels: int
    received_packets: int
    duplicate_packets: int
    lost_packets: int
    duration_seconds: float

    def write_wav(self, path: str | Path) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(self.channels)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(self.pcm)


_CODECS = {0: ("PCMU", 8000), 8: ("PCMA", 8000)}


def reconstruct_rtp(packets: Iterable[bytes | RTPPacket], *, max_duration_seconds: float = 300,
                    channels: int = 1, max_loss_ratio: float = 0.25) -> MediaResult:
    """Order and decode one RTP stream, accounting for duplicates and loss."""
    if channels not in (1, 2):
        raise MediaValidationError("only mono and stereo media are supported")
    parsed = [item if isinstance(item, RTPPacket) else RTPPacket.parse(item) for item in packets]
    if not parsed:
        raise IncompleteMediaError("RTP stream is empty", received=0, lost=0)
    ssrcs, types = {p.ssrc for p in parsed}, {p.payload_type for p in parsed}
    if len(ssrcs) != 1 or len(types) != 1:
        raise MediaValidationError("mixed RTP streams or codecs require separate channel reconstruction")
    codec_info = _CODECS.get(next(iter(types)))
    if codec_info is None:
        raise MediaValidationError(f"unsupported RTP payload type {next(iter(types))}")
    # RTP timestamps provide the media chronology even when packets arrive out
    # of order. Sequence is a deterministic tie-breaker for duplicates.
    parsed.sort(key=lambda p: (p.timestamp, p.sequence))
    unique: list[RTPPacket] = []
    seen: set[int] = set()
    duplicates = 0
    for packet in parsed:
        if packet.sequence in seen:
            duplicates += 1
        else:
            seen.add(packet.sequence)
            unique.append(packet)
    span = ((unique[-1].sequence - unique[0].sequence) & 0xFFFF) + 1
    lost = max(0, span - len(unique))
    if lost and lost / span > max_loss_ratio:
        raise IncompleteMediaError("RTP loss exceeds the configured completeness limit", received=len(unique), lost=lost)
    codec, rate = codec_info
    pcm_parts = [audioop.ulaw2lin(p.payload, 2) if codec == "PCMU" else audioop.alaw2lin(p.payload, 2) for p in unique]
    pcm = b"".join(pcm_parts)
    duration = len(pcm) / (2 * rate * channels)
    if duration > max_duration_seconds:
        raise MediaValidationError("media exceeds maximum Voice Monitor duration")
    return MediaResult(pcm, codec, rate, channels, len(unique), duplicates, lost, duration)


class PrivateMediaStore:
    """Opaque, optionally encrypted storage rooted outside application static files."""

    def __init__(self, root: str | Path | None = None, *, encryption_key: bytes | str | None = None) -> None:
        self.root = Path(root or os.getenv("VOICE_MONITOR_MEDIA_ROOT", "/var/lib/myportal/voice-monitor"))
        if "static" in {part.lower() for part in self.root.parts}:
            raise ValueError("Voice Monitor media cannot be stored under a public static path")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = encryption_key or os.getenv("VOICE_MONITOR_MEDIA_ENCRYPTION_KEY")
        self.cipher = Fernet(key.encode() if isinstance(key, str) else key) if key else None

    def put(self, company_id: int, content: bytes, *, created_at: datetime | None = None) -> str:
        opaque_id = secrets.token_urlsafe(32)
        data = self.cipher.encrypt(content) if self.cipher else content
        (self.root / opaque_id).write_bytes(data)
        os.chmod(self.root / opaque_id, 0o600)
        metadata = {"company_id": int(company_id), "created_at": (created_at or datetime.now(timezone.utc)).isoformat(), "encrypted": bool(self.cipher)}
        (self.root / f"{opaque_id}.json").write_text(json.dumps(metadata), encoding="utf-8")
        os.chmod(self.root / f"{opaque_id}.json", 0o600)
        return opaque_id

    def get(self, company_id: int, opaque_id: str) -> bytes:
        if not opaque_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in opaque_id):
            raise FileNotFoundError("media not found")
        try:
            metadata = json.loads((self.root / f"{opaque_id}.json").read_text(encoding="utf-8"))
            if int(metadata["company_id"]) != int(company_id):
                raise PermissionError("media belongs to another company")
            data = (self.root / opaque_id).read_bytes()
            return self.cipher.decrypt(data) if metadata.get("encrypted") and self.cipher else data
        except InvalidToken as exc:
            raise MediaValidationError("media decryption failed") from exc

    def delete_expired(self, *, retention_days: int, now: datetime | None = None) -> int:
        if retention_days < 0:
            raise ValueError("retention_days cannot be negative")
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
        deleted = 0
        for sidecar in self.root.glob("*.json"):
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(metadata["created_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if created <= cutoff:
                (self.root / sidecar.stem).unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                deleted += 1
        return deleted
