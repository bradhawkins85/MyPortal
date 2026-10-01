from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.voice_monitor import callbacks
from app.services.voice_monitor.policy import DialDenied, authorize_attempt


def test_callback_signature_timestamp_and_replay_idempotency():
    body = json.dumps({"event_id": "evt-1", "call_id": "call-1", "status": "completed"}).encode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signature = hmac.new(b"secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    with patch.object(callbacks.db, "execute_rowcount", new_callable=AsyncMock, side_effect=[1, 1]):
        assert asyncio.run(callbacks.handle_callback(body, signature, timestamp, "secret"))
    with pytest.raises(callbacks.InvalidCallback):
        callbacks.verify_signature(body, "wrong", timestamp, "secret")


def test_policy_fails_closed_without_consent_evidence():
    endpoint = {"enabled": 1, "consent_granted": 0}
    with patch("app.services.voice_monitor.policy.db.fetch_one", new_callable=AsyncMock, return_value=endpoint):
        with pytest.raises(DialDenied, match="consent"):
            asyncio.run(authorize_attempt({"endpoint_id": 1, "company_id": 2}, global_limit=10, tenant_limit=2))
