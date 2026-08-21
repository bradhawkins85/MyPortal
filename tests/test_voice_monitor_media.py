from __future__ import annotations

from datetime import datetime, timedelta, timezone
import struct

import pytest

from app.services.voice_monitor.media import (
    CaptureScope, IncompleteMediaError, MediaValidationError, PrivateMediaStore,
    reconstruct_rtp,
)


def packet(sequence: int, payload: bytes = b"\xff" * 160, payload_type: int = 0) -> bytes:
    return struct.pack("!BBHII", 0x80, payload_type, sequence, sequence * 160, 123) + payload


def test_pcmu_reordering_duplicates_and_loss():
    result = reconstruct_rtp([packet(3), packet(1), packet(3), packet(4)])
    assert result.codec == "PCMU"
    assert result.duplicate_packets == 1
    assert result.lost_packets == 1
    assert result.received_packets == 3


def test_pcma_supported():
    assert reconstruct_rtp([packet(1, payload_type=8)]).codec == "PCMA"


def test_malformed_incomplete_and_duration_limits():
    with pytest.raises(MediaValidationError):
        reconstruct_rtp([b"short"])
    with pytest.raises(IncompleteMediaError):
        reconstruct_rtp([])
    with pytest.raises(IncompleteMediaError):
        reconstruct_rtp([packet(1), packet(10)])
    with pytest.raises(MediaValidationError, match="maximum"):
        reconstruct_rtp([packet(1)], max_duration_seconds=.001)


def test_capture_scope_requires_call_ports_and_window():
    now = datetime.now(timezone.utc)
    scope = CaptureScope("call-opaque", frozenset({12000}), now, now + timedelta(seconds=5))
    assert scope.ports == {12000}
    with pytest.raises(MediaValidationError):
        CaptureScope("", frozenset(), now, now)


def test_private_store_authorization_encryption_and_retention(tmp_path):
    from cryptography.fernet import Fernet
    store = PrivateMediaStore(tmp_path / "private", encryption_key=Fernet.generate_key())
    ref = store.put(7, b"private audio", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert store.get(7, ref) == b"private audio"
    assert (tmp_path / "private" / ref).read_bytes() != b"private audio"
    with pytest.raises(PermissionError):
        store.get(8, ref)
    assert store.delete_expired(retention_days=30) == 1
    assert not (tmp_path / "private" / ref).exists()
