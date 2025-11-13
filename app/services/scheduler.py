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
from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.services import asset_importer
from app.services import automations as automations_service
from app.services import company_id_lookup
from app.services import imap as imap_service
from app.services import m365 as m365_service
from app.services import products as products_service
from app.services import staff_importer
from app.services import subscription_price_changes
from app.services import subscription_renewals
from app.services import tickets as tickets_service
from app.services import value_templates
from app.services import webhook_monitor
from app.services import xero as xero_service

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
        self._scheduler = AsyncIOScheduler(timezone=settings.default_timezone)
        self._started = False
        self._refresh_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        self._ensure_monitoring_jobs()
        self._start_refresh_task()
        log_info("Scheduler started")

    async def stop(self) -> None:
        if not self._started:
            return
        await self._await_refresh_task()
        self._scheduler.shutdown(wait=True)
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

    def _track_refresh_task(self, task: asyncio.Task[None]) -> None:
        self._refresh_task = task
        task.add_done_callback(self._handle_refresh_completion)

    def _handle_refresh_completion(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Scheduler refresh failed", error=str(exc))
        finally:
            if self._refresh_task is task:
                self._refresh_task = None

    def _start_refresh_task(self) -> None:
        task = asyncio.create_task(self.refresh())
        self._track_refresh_task(task)

    async def _await_refresh_task(self) -> None:
        task = self._refresh_task
        self._refresh_task = None
        if not task:
            return
        try:
            await task
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("Scheduler refresh failed during shutdown", error=str(exc))

    def _ensure_monitoring_jobs(self) -> None:
        if not self._started:
            return
        if not self._scheduler.get_job("webhook-monitor"):
            self._scheduler.add_job(
                self._run_webhook_monitor,
                "interval",
                seconds=60,
                id="webhook-monitor",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("webhook-cleanup"):
            self._scheduler.add_job(
                self._run_webhook_cleanup,
                "interval",
                hours=1,
                id="webhook-cleanup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("automation-runner"):
            self._scheduler.add_job(
                self._run_automation_runner,
                "interval",
                seconds=60,
                id="automation-runner",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Run subscription renewal job daily at 02:00 (store timezone)
        if not self._scheduler.get_job("subscription-renewals"):
            self._scheduler.add_job(
                self._run_subscription_renewals,
                CronTrigger(hour=2, minute=0, timezone=self._scheduler.timezone),
                id="subscription-renewals",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

    async def _run_webhook_monitor(self) -> None:
        """Run webhook monitoring with distributed lock to prevent duplicate execution."""
        async with db.acquire_lock("webhook_monitor", timeout=1) as lock_acquired:
            if not lock_acquired:
                log_info("Webhook monitor already running on another worker, skipping")
                return
            await webhook_monitor.fail_stalled_events(timeout_seconds=600)
            await webhook_monitor.process_pending_events()

    async def _run_webhook_cleanup(self) -> None:
        """Run webhook cleanup with distributed lock to prevent duplicate execution."""
        async with db.acquire_lock("webhook_cleanup", timeout=1) as lock_acquired:
            if not lock_acquired:
                log_info("Webhook cleanup already running on another worker, skipping")
                return
            await webhook_monitor.purge_completed_events()

    async def _run_automation_runner(self) -> None:
        """Run automation processing with distributed lock to prevent duplicate execution."""
        async with db.acquire_lock("automation_runner", timeout=1) as lock_acquired:
            if not lock_acquired:
                log_info("Automation runner already running on another worker, skipping")
                return
            await automations_service.process_due_automations()
    
    async def _run_subscription_renewals(self) -> None:
        """Run subscription renewal invoice creation (T-60 job) with distributed lock."""
        async with db.acquire_lock("subscription_renewals", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info("Subscription renewals already running on another worker, skipping")
                return
            
            from datetime import date
            today = date.today()
            log_info("Starting subscription renewal invoice creation", date=today)
            
            try:
                result = await subscription_renewals.create_renewal_invoices_for_date(today)
                log_info(
                    "Subscription renewal invoice creation completed",
                    **result
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error(
                    "Subscription renewal invoice creation failed",
                    date=today,
                    error=str(exc),
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
        
        if task_id is None:
            log_error("Scheduled task missing identifier", command=command)
            return

        # Use a distributed lock to ensure only one worker executes this task
        lock_name = f"scheduled_task_{task_id}"
        
        async with db.acquire_lock(lock_name, timeout=1) as lock_acquired:
            if not lock_acquired:
                # Another worker is already executing this task, skip silently
                log_info(
                    "Scheduled task already running on another worker, skipping",
                    task_id=task_id,
                    command=command,
                )
                return

            log_info("Running scheduled task", task_id=task_id, command=command)
            started_at = datetime.now(timezone.utc)
            status = "succeeded"
            details: str | None = None
            
            try:
                if command == "sync_staff":
                    company_id = task.get("company_id")
                    if company_id:
                        await staff_importer.import_contacts_for_company(int(company_id))
                elif command == "sync_assets":
                    company_id = task.get("company_id")
                    if company_id:
                        await asset_importer.import_assets_for_company(int(company_id))
                elif command == "sync_tactical_assets":
                    company_id = task.get("company_id")
                    if company_id:
                        processed = await asset_importer.import_tactical_assets_for_company(int(company_id))
                        details = json.dumps(
                            {"company_id": int(company_id), "processed": processed},
                            default=str,
                        )
                    else:
                        summary = await asset_importer.import_all_tactical_assets()
                        details = json.dumps(summary, default=str)
                elif command == "sync_o365":
                    company_id = task.get("company_id")
                    if company_id:
                        await m365_service.sync_company_licenses(int(company_id))
                elif command == "sync_to_xero":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await xero_service.sync_company(int(company_id))
                        if result:
                            details = json.dumps(result, default=str)
                            result_status = str(
                                result.get("status")
                                or result.get("event_status")
                                or ""
                            ).strip().lower()
                            if result_status in {"failed", "error"}:
                                status = "failed"
                            elif result_status == "skipped":
                                status = "skipped"
                        else:
                            details = None
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_to_xero_auto_send":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await xero_service.sync_company(int(company_id), auto_send=True)
                        if result:
                            details = json.dumps(result, default=str)
                            result_status = str(
                                result.get("status")
                                or result.get("event_status")
                                or ""
                            ).strip().lower()
                            if result_status in {"failed", "error"}:
                                status = "failed"
                            elif result_status == "skipped":
                                status = "skipped"
                        else:
                            details = None
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "refresh_company_ids":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await company_id_lookup.lookup_missing_company_ids(int(company_id))
                        details = json.dumps(result, default=str) if result else None
                    else:
                        result = await company_id_lookup.refresh_all_missing_company_ids()
                        details = json.dumps(result, default=str) if result else None
                elif command == "update_products":
                    await products_service.update_products_from_feed()
                elif command == "update_stock_feed":
                    await products_service.update_stock_feed()
                elif command == "system_update":
                    output = await self._run_system_update(force_restart=force_restart)
                    if output:
                        details = output
                elif command == "create_scheduled_ticket":
                    # Parse JSON payload from task description
                    task_description = task.get("description") or ""
                    try:
                        payload = json.loads(task_description) if task_description else {}
                    except json.JSONDecodeError as exc:
                        status = "failed"
                        details = f"Invalid JSON payload: {str(exc)}"
                        log_error(
                            "Invalid JSON in scheduled ticket creation",
                            task_id=task_id,
                            error=str(exc),
                        )
                    else:
                        # Render template variables in the payload
                        payload = await value_templates.render_value_async(payload, context=None)
                        
                        # Extract ticket fields from payload
                        subject = payload.get("subject", "")
                        if not subject:
                            status = "failed"
                            details = "Missing required field: subject"
                        else:
                            try:
                                company_id = task.get("company_id")
                                ticket = await tickets_service.create_ticket(
                                    subject=str(subject),
                                    description=payload.get("description"),
                                    company_id=int(company_id) if company_id else None,
                                    requester_id=int(payload.get("requester_id")) if payload.get("requester_id") else None,
                                    assigned_user_id=int(payload.get("assigned_user_id")) if payload.get("assigned_user_id") else None,
                                    priority=str(payload.get("priority", "normal")),
                                    status=str(payload.get("status", "open")),
                                    category=str(payload.get("category")) if payload.get("category") else None,
                                    module_slug=str(payload.get("module_slug")) if payload.get("module_slug") else None,
                                    external_reference=str(payload.get("external_reference")) if payload.get("external_reference") else None,
                                    trigger_automations=False,  # Prevent automation loops
                                )
                                ticket_id = ticket.get("id") if ticket else None
                                ticket_number = ticket.get("number") if ticket else None
                                details = json.dumps({
                                    "ticket_id": ticket_id,
                                    "ticket_number": ticket_number,
                                    "subject": subject,
                                }, default=str)
                                log_info(
                                    "Scheduled ticket created",
                                    task_id=task_id,
                                    ticket_id=ticket_id,
                                    ticket_number=ticket_number,
                                )
                            except Exception as ticket_exc:
                                status = "failed"
                                details = f"Ticket creation failed: {str(ticket_exc)}"
                                log_error(
                                    "Failed to create scheduled ticket",
                                    task_id=task_id,
                                    error=str(ticket_exc),
                                )
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
                elif command == "send_price_change_notifications":
                    result = await subscription_price_changes.send_price_change_notifications()
                    details = json.dumps(result, default=str)
                    log_info("Price change notifications sent", **result)
                elif command == "apply_scheduled_price_changes":
                    result = await subscription_price_changes.apply_scheduled_price_changes()
                    details = json.dumps(result, default=str)
                    log_info("Scheduled price changes applied", **result)
                elif command == "sync_recordings":
                    from app.services import call_recordings as call_recordings_service
                    from app.repositories import integration_modules as modules_repo
                    
                    # Get recordings path from module settings
                    module = await modules_repo.get_module("call-recordings")
                    if module and module.get("settings"):
                        recordings_path = module["settings"].get("recordings_path")
                        if recordings_path:
                            result = await call_recordings_service.sync_recordings_from_filesystem(recordings_path)
                            details = json.dumps(result, default=str)
                            log_info("Call recordings synced", **result)
                        else:
                            status = "skipped"
                            details = "No recordings path configured in call-recordings module"
                            log_info("Call recordings sync skipped", reason=details)
                    else:
                        status = "skipped"
                        details = "Call recordings module not configured"
                        log_info("Call recordings sync skipped", reason=details)
                elif command == "queue_transcriptions":
                    from app.services import call_recordings as call_recordings_service
                    
                    result = await call_recordings_service.queue_pending_transcriptions()
                    details = json.dumps(result, default=str)
                    log_info("Transcriptions queued", **result)
                elif command == "process_transcription":
                    from app.services import call_recordings as call_recordings_service
                    
                    result = await call_recordings_service.process_queued_transcriptions()
                    details = json.dumps(result, default=str)
                    if result.get("status") == "error":
                        status = "failed"
                    log_info("Transcription processed", **result)
                elif command == "bcp_notify_upcoming_training":
                    # Notify about upcoming BCP training sessions
                    from app.repositories import bcp as bcp_repo
                    from app.repositories import notifications as notifications_repo
                    
                    # Get training items in the next 7 days (configurable via task description)
                    task_description = task.get("description") or ""
                    try:
                        config = json.loads(task_description) if task_description else {}
                        days_ahead = config.get("days_ahead", 7)
                    except json.JSONDecodeError:
                        days_ahead = 7
                    
                    upcoming = await bcp_repo.get_upcoming_training_items(days_ahead=days_ahead)
                    
                    if upcoming:
                        for item in upcoming:
                            plan = item.get("plan", {})
                            plan_id = plan.get("id")
                            
                            if plan_id:
                                # Get distribution list for the plan
                                distribution_list = await bcp_repo.list_distribution_list(plan_id)
                                
                                # Create notification for each distribution list member
                                message = f"Upcoming BCP training scheduled for {item['training_date'].strftime('%Y-%m-%d %H:%M')}"
                                if item.get("training_type"):
                                    message += f" - {item['training_type']}"
                                
                                # Create notification for all users (could be refined to specific users)
                                await notifications_repo.create_notification(
                                    event_type="bcp_training_reminder",
                                    message=message,
                                    user_id=None,  # Broadcast to all users
                                    metadata={
                                        "plan_id": plan_id,
                                        "plan_title": plan.get("title"),
                                        "training_id": item["id"],
                                        "training_date": item["training_date"].isoformat(),
                                        "training_type": item.get("training_type"),
                                    }
                                )
                        
                        details = json.dumps(
                            {"upcoming_training_count": len(upcoming), "days_ahead": days_ahead},
                            default=str
                        )
                        log_info("BCP training reminders sent", count=len(upcoming))
                    else:
                        status = "skipped"
                        details = f"No upcoming training in next {days_ahead} days"
                        log_info("No upcoming BCP training to notify", days_ahead=days_ahead)
                elif command == "bcp_notify_upcoming_review":
                    # Notify about upcoming BCP plan reviews
                    from app.repositories import bcp as bcp_repo
                    from app.repositories import notifications as notifications_repo
                    
                    # Get review items in the next 7 days (configurable via task description)
                    task_description = task.get("description") or ""
                    try:
                        config = json.loads(task_description) if task_description else {}
                        days_ahead = config.get("days_ahead", 7)
                    except json.JSONDecodeError:
                        days_ahead = 7
                    
                    upcoming = await bcp_repo.get_upcoming_review_items(days_ahead=days_ahead)
                    
                    if upcoming:
                        for item in upcoming:
                            plan = item.get("plan", {})
                            plan_id = plan.get("id")
                            
                            if plan_id:
                                # Get distribution list for the plan
                                distribution_list = await bcp_repo.list_distribution_list(plan_id)
                                
                                # Create notification
                                message = f"Upcoming BCP plan review scheduled for {item['review_date'].strftime('%Y-%m-%d %H:%M')}"
                                if item.get("reason"):
                                    message += f" - {item['reason']}"
                                
                                # Create notification for all users (could be refined to specific users)
                                await notifications_repo.create_notification(
                                    event_type="bcp_review_reminder",
                                    message=message,
                                    user_id=None,  # Broadcast to all users
                                    metadata={
                                        "plan_id": plan_id,
                                        "plan_title": plan.get("title"),
                                        "review_id": item["id"],
                                        "review_date": item["review_date"].isoformat(),
                                        "reason": item.get("reason"),
                                    }
                                )
                        
                        details = json.dumps(
                            {"upcoming_review_count": len(upcoming), "days_ahead": days_ahead},
                            default=str
                        )
                        log_info("BCP review reminders sent", count=len(upcoming))
                    else:
                        status = "skipped"
                        details = f"No upcoming reviews in next {days_ahead} days"
                        log_info("No upcoming BCP reviews to notify", days_ahead=days_ahead)
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
            else:
                env["FORCE_RESTART"] = "0"
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
