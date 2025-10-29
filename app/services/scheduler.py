from __future__ import annotations

import asyncio
import json
import os
from asyncio.subprocess import PIPE
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.services import asset_importer
from app.services import automations as automations_service
from app.services import imap as imap_service
from app.services import staff_importer
from app.services import m365 as m365_service
from app.services import products as products_service
from app.services import webhook_monitor

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SYSTEM_UPDATE_LOCK = asyncio.Lock()
_OUTPUT_PREVIEW_LIMIT = 2000


def _truncate_output(payload: str | bytes | None) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = payload
    text = text.strip()
    if not text:
        return None
    if len(text) <= _OUTPUT_PREVIEW_LIMIT:
        return text
    return text[: _OUTPUT_PREVIEW_LIMIT - 1] + "\u2026"


class SchedulerService:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._scheduler = AsyncIOScheduler(timezone=settings.default_timezone)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        self._ensure_monitoring_jobs()
        await self.refresh()
        log_info("Scheduler started")

    async def stop(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        log_info("Scheduler stopped")

    async def refresh(self) -> None:
        if not self._started:
            return
        for job in list(self._scheduler.get_jobs()):
            if job.id and job.id.startswith("scheduled-task-"):
                job.remove()
        tasks = await scheduled_tasks_repo.list_active_tasks()
        for task in tasks:
            trigger = self._build_trigger(task)
            if not trigger:
                continue
            self._scheduler.add_job(
                self._run_task,
                trigger=trigger,
                args=[task],
                id=f"scheduled-task-{task['id']}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        log_info("Scheduler tasks loaded", count=len(tasks))
        self._ensure_monitoring_jobs()

    def _ensure_monitoring_jobs(self) -> None:
        if not self._started:
            return
        if not self._scheduler.get_job("webhook-monitor"):
            self._scheduler.add_job(
                webhook_monitor.process_pending_events,
                "interval",
                seconds=60,
                id="webhook-monitor",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("webhook-cleanup"):
            self._scheduler.add_job(
                webhook_monitor.purge_completed_events,
                "interval",
                hours=1,
                id="webhook-cleanup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("automation-runner"):
            interval = max(5, min(int(self._settings.automation_runner_interval_seconds), 3600))
            self._scheduler.add_job(
                automations_service.process_due_automations,
                "interval",
                seconds=interval,
                id="automation-runner",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

    def _build_trigger(self, task: dict[str, Any]) -> CronTrigger | None:
        try:
            return CronTrigger.from_crontab(task["cron"], timezone=self._scheduler.timezone)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to parse cron expression",
                task_id=task.get("id"),
                cron=task.get("cron"),
                error=str(exc),
            )
            return None

    async def _run_task(self, task: dict[str, Any], *, force_restart: bool = False) -> None:
        task_id = task.get("id")
        command = task.get("command")
        log_info("Running scheduled task", task_id=task_id, command=command)
        started_at = datetime.now(timezone.utc)
        status = "succeeded"
        details: str | None = None
        if task_id is None:
            log_error("Scheduled task missing identifier", command=command)
            return
        try:
            if command == "sync_staff":
                company_id = task.get("company_id")
                if company_id:
                    await staff_importer.import_contacts_for_company(int(company_id))
            elif command == "sync_assets":
                company_id = task.get("company_id")
                if company_id:
                    await asset_importer.import_assets_for_company(int(company_id))
            elif command == "sync_o365":
                company_id = task.get("company_id")
                if company_id:
                    await m365_service.sync_company_licenses(int(company_id))
            elif command == "update_products":
                await products_service.update_products_from_feed()
            elif command == "update_stock_feed":
                await products_service.update_stock_feed()
            elif command == "system_update":
                output = await self._run_system_update(force_restart=force_restart)
                if output:
                    details = output
            elif isinstance(command, str) and command.startswith("imap_sync:"):
                try:
                    account_id = int(command.split(":", 1)[1])
                except (IndexError, ValueError):
                    status = "skipped"
                    details = "Invalid IMAP account reference"
                    log_error(
                        "Invalid IMAP sync command",
                        task_id=task_id,
                        command=command,
                    )
                else:
                    result = await imap_service.sync_account(account_id)
                    details = json.dumps(result, default=str) if result else None
            else:
                status = "skipped"
                details = "No handler registered for command"
                log_info("Scheduled task has no handler", task_id=task_id, command=command)
        except Exception as exc:  # pragma: no cover - defensive logging
            status = "failed"
            details = str(exc)
            log_error(
                "Scheduled task failed",
                task_id=task_id,
                command=command,
                error=str(exc),
            )
        finally:
            finished_at = datetime.now(timezone.utc)
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            await scheduled_tasks_repo.record_task_run(
                int(task_id),
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                details=details,
            )

    async def run_now(self, task_id: int) -> None:
        task = await scheduled_tasks_repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        await self._run_task(task, force_restart=True)

    async def _run_system_update(self, *, force_restart: bool = False) -> str | None:
        script_path = _PROJECT_ROOT / "scripts" / "upgrade.sh"
        if not script_path.exists():
            raise FileNotFoundError("System update script not found")
        if not os.access(script_path, os.X_OK):
            raise PermissionError("System update script is not executable")

        async with _SYSTEM_UPDATE_LOCK:
            log_info("Starting system update", script=str(script_path))
            if force_restart:
                log_info("System update run requested from UI; forcing restart helper execution")
            env = os.environ.copy()
            if force_restart:
                env["FORCE_RESTART"] = "1"
            process = await asyncio.create_subprocess_exec(
                str(script_path),
                stdout=PIPE,
                stderr=PIPE,
                cwd=str(_PROJECT_ROOT),
                env=env,
            )
            stdout, stderr = await process.communicate()
            stdout_preview = _truncate_output(stdout)
            stderr_preview = _truncate_output(stderr)

            if stdout_preview:
                log_info("System update output", preview=stdout_preview)
            if stderr_preview:
                log_info("System update stderr", preview=stderr_preview)

            if process.returncode != 0:
                message = stderr_preview or stdout_preview or "Unknown error"
                raise RuntimeError(
                    f"System update script exited with code {process.returncode}: {message}"
                )

            log_info("System update completed", exit_code=process.returncode)
            return stdout_preview


scheduler_service = SchedulerService()
