from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.voice_monitor.providers import CallState
from app.services.voice_monitor.providers.sip import SipProvider
from app.workers.voice_monitor import VoiceMonitorWorker


def _environment() -> dict[str, str]:
    return {
        "SIP_SERVER": "sip.example.test",
        "SIP_USERNAME": "monitor",
        "SIP_PASSWORD": "secret",
    }


def test_sip_provider_requires_installed_client_and_credentials():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="SIP_SERVER"):
            SipProvider()
    with patch.dict("os.environ", _environment(), clear=True), patch(
        "app.services.voice_monitor.providers.sip.shutil.which", return_value=None
    ):
        with pytest.raises(RuntimeError, match="not installed"):
            SipProvider()


def test_sip_provider_originates_local_baresip_process():
    async def scenario():
        process = MagicMock()
        process.returncode = None
        process.stdout.readline = AsyncMock(return_value=b"")
        process.wait = AsyncMock(return_value=0)
        with patch.dict("os.environ", _environment(), clear=True), patch(
            "app.services.voice_monitor.providers.sip.shutil.which", return_value="/usr/bin/baresip"
        ), patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as create:
            provider = SipProvider()
            call = await provider.originate("+61412345678", idempotency_key="attempt-1")
            await provider._calls[call.call_id].reader
        command = create.call_args.args
        assert command[:3] == ("baresip", "-f", command[2])
        assert command[3:] == ("-e", "/dial sip:61412345678@sip.example.test:5060;transport=udp")
        assert await provider.status(call.call_id) is CallState.FAILED

    asyncio.run(scenario())


def test_worker_enforces_global_maximum_call_duration():
    worker = VoiceMonitorWorker(MagicMock())
    with patch.dict("os.environ", {"VOICE_MONITOR_MAX_CALL_DURATION_SECONDS": "60"}):
        assert worker._call_timeout({"timeout_seconds": 240}) == 60
        assert worker._call_timeout({"timeout_seconds": 20}) == 20
