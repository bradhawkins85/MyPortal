"""Server-local SIP provider backed by the ``baresip`` command-line client."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.services.voice_monitor.providers import CallState, MediaArtifact, OriginatedCall


@dataclass
class _Call:
    process: asyncio.subprocess.Process
    state: CallState = CallState.RINGING
    reader: asyncio.Task[None] | None = None


class SipProvider:
    """Originate calls directly from this host using an isolated baresip process."""

    def __init__(self) -> None:
        self.server = _required("SIP_SERVER")
        self.username = _required("SIP_USERNAME")
        self.password = _required("SIP_PASSWORD")
        self.caller_id = os.getenv("SIP_CALLER_ID", self.username).strip()
        self.transport = os.getenv("SIP_TRANSPORT", "udp").strip().lower()
        if self.transport not in {"udp", "tcp", "tls"}:
            raise ValueError("SIP_TRANSPORT must be udp, tcp, or tls")
        self.port = int(os.getenv("SIP_PORT", "5061" if self.transport == "tls" else "5060"))
        self.executable = os.getenv("SIP_CLIENT_BINARY", "baresip")
        if shutil.which(self.executable) is None:
            raise RuntimeError(f"SIP client is not installed: {self.executable}")
        self._calls: dict[str, _Call] = {}

    async def originate(self, destination: str, *, idempotency_key: str) -> OriginatedCall:
        existing = self._calls.get(idempotency_key)
        if existing and existing.process.returncode is None:
            return OriginatedCall(idempotency_key, existing.state)

        config_dir = Path(tempfile.mkdtemp(prefix="myportal-sip-"))
        config_dir.chmod(0o700)
        account = (
            f'"{self.caller_id}" <sip:{self.username}@{self.server}:{self.port};transport={self.transport}>'
            f";auth_user={self.username};auth_pass={self.password}\n"
        )
        (config_dir / "accounts").write_text(account, encoding="utf-8")
        (config_dir / "accounts").chmod(0o600)
        target = f"sip:{destination.lstrip('+')}@{self.server}:{self.port};transport={self.transport}"
        process = await asyncio.create_subprocess_exec(
            self.executable, "-f", str(config_dir), "-e", f"/dial {target}",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        call = _Call(process)
        self._calls[idempotency_key] = call
        call.reader = asyncio.create_task(self._read_output(idempotency_key, call, config_dir))
        return OriginatedCall(idempotency_key)

    async def _read_output(self, call_id: str, call: _Call, config_dir: Path) -> None:
        assert call.process.stdout is not None
        try:
            while line := await call.process.stdout.readline():
                text = line.decode(errors="replace").casefold()
                if "call established" in text or "answered" in text:
                    call.state = CallState.ANSWERED
                elif re.search(r"busy|486", text):
                    call.state = CallState.BUSY
                elif re.search(r"no answer|408|480", text):
                    call.state = CallState.NO_ANSWER
                elif "call closed" in text or "session closed" in text:
                    call.state = CallState.COMPLETED
            await call.process.wait()
            if call.state not in {CallState.COMPLETED, CallState.BUSY, CallState.NO_ANSWER}:
                call.state = CallState.FAILED
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

    async def status(self, call_id: str) -> CallState:
        call = self._calls.get(call_id)
        return call.state if call else CallState.FAILED

    async def hangup(self, call_id: str) -> None:
        call = self._calls.get(call_id)
        if not call or call.process.returncode is not None:
            return
        if call.process.stdin:
            call.process.stdin.write(b"/hangup\n/quit\n")
            await call.process.stdin.drain()
        try:
            await asyncio.wait_for(call.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            call.process.kill()
            await call.process.wait()

    def map_callback(self, payload: Mapping[str, object]) -> CallState:
        return CallState(str(payload.get("state", "failed")))

    async def retrieve_media(self, call_id: str) -> MediaArtifact | None:
        return None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the SIP provider")
    return value


def create_provider() -> SipProvider:
    return SipProvider()
