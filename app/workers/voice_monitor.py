"""Independent voice monitor worker entry point."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import signal
import socket
from contextlib import suppress
from pathlib import Path
from typing import Callable

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import voice_monitor as repository
from app.services.redis import close_redis_client
from app.services.voice_monitor.providers import CallState, VoiceMonitorProvider
from app.services.voice_monitor.policy import DialDenied, authorize_attempt
from app.services.transcription import TranscriptionUnavailable, WhisperXClient


class VoiceMonitorWorker:
    def __init__(self, provider: VoiceMonitorProvider, *, global_limit: int = 20,
                 tenant_limit: int = 3, lease_seconds: int = 90,
                 poll_seconds: float = 2.0, identity: str | None = None) -> None:
        self.provider = provider
        self.global_limit = global_limit
        self.tenant_limit = tenant_limit
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.identity = identity or f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._post_tasks: set[asyncio.Task[None]] = set()
        self._global = asyncio.Semaphore(global_limit)
        self._tenants: dict[int, asyncio.Semaphore] = {}

    async def run(self) -> None:
        while not self.stopping.is_set():
            await repository.record_worker_heartbeat(self.identity, active_calls=len(self._tasks))
            capacity = self.global_limit - len(self._tasks)
            if capacity > 0:
                attempts = await repository.claim_attempts(
                    worker_identity=self.identity, limit=capacity,
                    lease_seconds=self.lease_seconds, per_tenant=self.tenant_limit,
                )
                for attempt in attempts:
                    task = asyncio.create_task(self._deliver(attempt))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            try:
                await authorize_attempt(attempt, global_limit=self.global_limit, tenant_limit=self.tenant_limit)
                await asyncio.wait_for(self.stopping.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._post_tasks:
            await asyncio.gather(*self._post_tasks, return_exceptions=True)

    async def stop(self, *, grace_seconds: float = 25) -> None:
        """Stop claiming, then reconcile every call that exceeds the grace."""
        self.stopping.set()
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _deliver(self, attempt: dict) -> None:
        tenant = self._tenants.setdefault(int(attempt["company_id"]), asyncio.Semaphore(self.tenant_limit))
        call_id: str | None = attempt.get("provider_call_id")
        heartbeat: asyncio.Task[None] | None = None
        async with self._global, tenant:
            try:
                # Reusing the durable key makes redelivery safe even if the worker
                # died after the provider accepted originate but before persistence.
                call = await self.provider.originate(
                    attempt["destination_e164"], idempotency_key=attempt["provider_idempotency_key"]
                )
                call_id = call.call_id
                await db.execute_rowcount(
                    "UPDATE voice_monitor_attempts SET provider_call_id = %s WHERE id = %s "
                    "AND lease_owner = %s AND provider_call_id IS NULL",
                    (call_id, attempt["id"], self.identity),
                )
                heartbeat = asyncio.create_task(self._heartbeat(int(attempt["id"])))
                timeout = float(attempt.get("timeout_seconds") or 30)
                state = await asyncio.wait_for(self._await_terminal(call_id), timeout=timeout)
                status = "passed" if state is CallState.COMPLETED else "failed"
                media = await self.provider.retrieve_media(call_id)
                if media is not None and attempt.get("recording_consent_granted"):
                    await repository.initialize_content(
                        int(attempt["id"]), int(attempt["company_id"]),
                        media_reference=media.reference[:255],
                        transcription_requested=self._transcription_permitted(attempt),
                        retention_days=int(os.getenv("VOICE_MONITOR_RETENTION_DAYS", "30")),
                    )
                    await db.execute_rowcount(
                        "UPDATE voice_monitor_attempts SET media_artifact_reference = %s "
                        "WHERE id = %s AND lease_owner = %s",
                        (media.reference[:512], attempt["id"], self.identity),
                    )
                await repository.finish_delivery(attempt, self.identity, status=status,
                                                  failure_category=None if status == "passed" else state.value)
                # Transcription is deliberately post-call and detached.  It cannot
                # delay hangup or turn a successful phone check into a call failure.
                if status == "passed" and media is not None and self._transcription_permitted(attempt):
                    task = asyncio.create_task(self._transcribe_media(attempt, media.reference))
                    self._post_tasks.add(task)
                    task.add_done_callback(self._post_tasks.discard)
            except asyncio.CancelledError:
                if call_id:
                    with suppress(Exception):
                        await self.provider.hangup(call_id)
                await repository.finish_delivery(attempt, self.identity, status="interrupted",
                                                  failure_category="worker_shutdown")
                raise
            except asyncio.TimeoutError:
                if call_id:
                    with suppress(Exception):
                        await self.provider.hangup(call_id)
                await repository.finish_delivery(attempt, self.identity, status="timed_out",
                                                  failure_category="call_timeout")
            except DialDenied:
                await repository.finish_delivery(attempt, self.identity, status="cancelled",
                                                  failure_category="policy_denied")
            except Exception as exc:
                # Log the exception type only: provider payloads can contain secrets.
                log_error("Voice monitor delivery failed", attempt_id=attempt["id"],
                          error_type=type(exc).__name__)
                await repository.finish_delivery(attempt, self.identity, status="failed",
                                                  failure_category="provider_error")
            finally:
                if heartbeat:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat

    @staticmethod
    def _transcription_permitted(attempt: dict) -> bool:
        return bool(attempt.get("transcription_enabled")) and bool(
            attempt.get("subscription_transcription_enabled", True)
        )

    async def _transcribe_media(self, attempt: dict, media_reference: str) -> None:
        attempt_id = int(attempt["id"])
        await repository.set_transcription_status(attempt_id, "processing")
        try:
            result = await WhisperXClient().transcribe(media_reference)
            await repository.store_transcript(
                attempt_id, int(attempt["company_id"]), result.text
            )
        except TranscriptionUnavailable as exc:
            # Store only a classification; exception strings may contain remote details.
            await repository.set_transcription_status(
                attempt_id, "failed", failure_code=type(exc).__name__
            )
        except Exception as exc:
            log_error("Voice monitor transcription failed", attempt_id=attempt_id,
                      error_type=type(exc).__name__)
            await repository.set_transcription_status(
                attempt_id, "failed", failure_code="transcription_error"
            )

    async def _heartbeat(self, attempt_id: int) -> None:
        while True:
            await asyncio.sleep(max(self.lease_seconds / 3, 1))
            if not await repository.heartbeat_attempt(
                attempt_id, self.identity, lease_seconds=self.lease_seconds
            ):
                return

    async def _await_terminal(self, call_id: str) -> CallState:
        while True:
            state = await self.provider.status(call_id)
            if state in {CallState.COMPLETED, CallState.BUSY, CallState.NO_ANSWER, CallState.FAILED}:
                return state
            await asyncio.sleep(1)


def _load_provider(path: str) -> VoiceMonitorProvider:
    module_name, factory_name = path.rsplit(":", 1)
    factory: Callable[[], VoiceMonitorProvider] = getattr(importlib.import_module(module_name), factory_name)
    return factory()


async def _main(provider_path: str, health_file: Path) -> None:
    await db.connect()
    worker = VoiceMonitorWorker(_load_provider(provider_path))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))
    health_file.parent.mkdir(parents=True, exist_ok=True)
    health_file.write_text(worker.identity)
    try:
        await worker.run()
    finally:
        health_file.unlink(missing_ok=True)
        await close_redis_client()
        await db.disconnect()
        log_info("Voice monitor worker stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=os.environ.get("VOICE_MONITOR_PROVIDER"), required=False)
    parser.add_argument("--health-file", type=Path, default=Path("/run/myportal/voice-monitor.health"))
    args = parser.parse_args()
    if not args.provider:
        parser.error("--provider or VOICE_MONITOR_PROVIDER is required")
    asyncio.run(_main(args.provider, args.health_file))
