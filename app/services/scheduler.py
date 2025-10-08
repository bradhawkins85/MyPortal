from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.services import staff_importer
from app.services import m365 as m365_service
from app.services import products as products_service
from app.services import webhook_monitor


class SchedulerService:
    def __init__(self) -> None:
        settings = get_settings()
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

    async def _run_task(self, task: dict[str, Any]) -> None:
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
            elif command == "sync_o365":
                company_id = task.get("company_id")
                if company_id:
                    await m365_service.sync_company_licenses(int(company_id))
            elif command == "update_products":
                await products_service.update_products_from_feed()
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
        await self._run_task(task)


scheduler_service = SchedulerService()
