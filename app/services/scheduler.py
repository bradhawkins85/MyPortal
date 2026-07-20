from __future__ import annotations

import asyncio
import json
import os
import re
from asyncio.subprocess import PIPE
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import m365 as m365_repo
from app.services import asset_importer
from app.services import automations as automations_service
from app.services import company_id_lookup
from app.services import imap as imap_service
from app.services import invoice_generator as invoice_generator_service
from app.services import m365 as m365_service
from app.services import modules as modules_service
from app.services import products as products_service
from app.services import staff_importer
from app.services import (
    staff_onboarding_workflows as staff_onboarding_workflows_service,
)
from app.services import subscription_price_changes
from app.services import subscription_renewals
from app.services import tickets as tickets_service
from app.services import tray_installer as tray_installer_service
from app.services import unbill_time_entries as unbill_time_entries_service
from app.services import value_templates
from app.services import webhook_monitor
from app.services import xero as xero_service
from app.services import service_status as service_status_service
from app.services import ticket_shipment_tracking as shipment_watch_service
from app.services import backup_jobs as backup_jobs_service
from app.repositories import rag_index as rag_index_repo
from app.repositories import rag_relationships as rag_relationship_repo

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SYSTEM_UPDATE_LOCK = asyncio.Lock()
_OUTPUT_PREVIEW_LIMIT = 2000
_SYSTEM_UPDATE_FLAG_PATH = _PROJECT_ROOT / "var" / "state" / "system_update.flag"
_DEFAULT_UPGRADE_MODE = "graceful"
_VALID_UPGRADE_MODES = {"graceful", "rolling", "restart"}
# Flag file that ``scripts/upgrade.sh`` writes when it pulls a
# feature-pack-only diff.  The scheduler polls it on a short interval
# and reloads each listed slug in-process so the running app picks up
# the new on-disk code without a service restart.  See
# ``docs/wiki/developer/Feature Packs.md``.
_FEATURE_PACK_RELOAD_FLAG_PATH = (
    _PROJECT_ROOT / "var" / "state" / "feature_pack_reload.flag"
)

# Directory prefix used to detect changes that are isolated to a single
# feature pack (see ``app/core/features.py``).  When every file in a
# ``system_update`` diff lives under ``app/features/<slug>/`` and each
# touched pack is currently loaded, the update is applied via the
# in-process feature-pack reload API instead of restarting the whole
# application.  The ``PACK.version`` literal is read for logging only —
# it is no longer a gate, so forgetting to bump it does not block
# hot-reload.
_FEATURE_PACKS_DIR_PREFIX = "app/features/"

# Matches ``version="1.2.3"`` / ``version='1.2.3'`` in a feature pack's
# ``__init__.py`` ``PACK = FeaturePack(...)`` declaration.  Captured
# for diagnostic logging on hot-reload; packs that compute ``version``
# dynamically simply get an empty match and fall through to the normal
# full-restart upgrade path.
_PACK_VERSION_RE = re.compile(r"""version\s*=\s*['"]([^'"]+)['"]""")

# Mapping of module slug -> set of scheduled task commands that require that module.
# Used to filter available commands in the UI and to disable tasks when a module is disabled.
COMMANDS_BY_MODULE: dict[str, set[str]] = {
    "m365": {
        "sync_m365_data",
        "sync_o365",
        "sync_m365_email_domains",
        "sync_m365_licenses",
        "sync_m365_contacts",
        "sync_m365_mailboxes",
        "refresh_m365_consent_status",
    },
    "xero": {"sync_to_xero", "sync_to_xero_auto_send"},
    "call-recordings": {
        "sync_recordings",
        "queue_transcriptions",
        "process_transcription",
    },
    "unifi-talk": {"sync_unifi_talk_recordings"},
    "tacticalrmm": {"push_tactical_companies", "pull_tactical_companies"},
    "huntress": {"sync_huntress"},
}


def _normalise_upgrade_mode(value: str | None) -> str:
    if not value:
        return _DEFAULT_UPGRADE_MODE
    mode = value.strip().lower()
    return mode if mode in _VALID_UPGRADE_MODES else _DEFAULT_UPGRADE_MODE


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


def _normalise_cron_day_field(day_field: str) -> str:
    parts = [part.strip() for part in day_field.split(",")]
    normalised_parts = ["last" if part.upper() == "L" else part for part in parts]
    return ",".join(normalised_parts)


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
        await self._ensure_monitoring_jobs()
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
        await self._ensure_monitoring_jobs()

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

    async def _ensure_monitoring_jobs(self) -> None:
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
        if not self._scheduler.get_job("staff-workflow-due-runner"):
            self._scheduler.add_job(
                self._run_staff_workflow_due_runner,
                "interval",
                seconds=60,
                id="staff-workflow-due-runner",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("staff-workflow-license-resume-runner"):
            self._scheduler.add_job(
                self._run_staff_workflow_license_resume_runner,
                "interval",
                seconds=60,
                id="staff-workflow-license-resume-runner",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("service-status-ai-lookup"):
            self._scheduler.add_job(
                self._run_service_status_ai_lookup,
                "interval",
                seconds=60,
                id="service-status-ai-lookup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        if not self._scheduler.get_job("ticket-shipment-watch-runner"):
            self._scheduler.add_job(
                self._run_ticket_shipment_watch_runner,
                "interval",
                seconds=60,
                id="ticket-shipment-watch-runner",
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
        # Run M365 credential renewal check daily at 03:00
        if not self._scheduler.get_job("m365-credential-renewal"):
            self._scheduler.add_job(
                self._run_m365_credential_renewal,
                CronTrigger(hour=3, minute=0, timezone=self._scheduler.timezone),
                id="m365-credential-renewal",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Daily Huntress snapshot sync (04:00 store-local). Pulls EDR / ITDR /
        # SAT / SIEM / SOC stats for every company linked to a Huntress org.
        if not self._scheduler.get_job("huntress-daily-sync"):
            self._scheduler.add_job(
                self._run_huntress_daily_sync,
                CronTrigger(hour=4, minute=0, timezone=self._scheduler.timezone),
                id="huntress-daily-sync",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Seed an "unknown" event for every active backup job each morning so
        # missing reports remain visible. Runs at 00:05 store-local time.
        if not self._scheduler.get_job("backup-history-seed"):
            self._scheduler.add_job(
                self._run_backup_history_seed,
                CronTrigger(hour=0, minute=5, timezone=self._scheduler.timezone),
                id="backup-history-seed",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Check backup job alert thresholds and create tickets when exceeded.
        # Runs at midnight store-local time (after the seed task at 00:05).
        if not self._scheduler.get_job("backup-alert-check"):
            self._scheduler.add_job(
                self._run_backup_alert_check,
                CronTrigger(hour=0, minute=15, timezone=self._scheduler.timezone),
                id="backup-alert-check",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Reconcile Solidtime state on a configurable interval. The reconciler
        # is a no-op when the integration module is disabled or unconfigured.
        if not self._scheduler.get_job("solidtime-reconcile"):
            from app.services import solidtime as solidtime_service

            solidtime_interval_minutes = (
                await solidtime_service.get_reconcile_interval_minutes()
            )
            self._scheduler.add_job(
                self._run_solidtime_reconcile,
                "interval",
                minutes=solidtime_interval_minutes,
                id="solidtime-reconcile",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Poll the feature-pack reload flag written by scripts/upgrade.sh
        # when it pulls a pack-only diff. Reloads the listed packs
        # in-process so the running app picks up the new code without
        # restarting. See ``_consume_feature_pack_reload_flag``.
        if not self._scheduler.get_job("feature-pack-reload-flag"):
            self._scheduler.add_job(
                self._consume_feature_pack_reload_flag,
                "interval",
                seconds=30,
                id="feature-pack-reload-flag",
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
                log_info(
                    "Automation runner already running on another worker, skipping"
                )
                return
            await automations_service.process_due_automations()

    async def _run_staff_workflow_due_runner(self) -> None:
        """Run due approved staff workflow executions with distributed lock."""
        async with db.acquire_lock(
            "staff_workflow_due_runner", timeout=1
        ) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Staff workflow due runner already running on another worker, skipping"
                )
                return
            result = (
                await staff_onboarding_workflows_service.process_due_approved_executions()
            )
            if result.get("processed", 0) or result.get("skipped", 0):
                log_info("Staff workflow due runner processed executions", **result)

    async def _run_staff_workflow_license_resume_runner(self) -> None:
        """Resume paused license-exhausted workflows when capacity becomes available."""
        async with db.acquire_lock(
            "staff_workflow_license_resume_runner", timeout=1
        ) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Staff workflow license resume runner already running on another worker, skipping"
                )
                return
            result = (
                await staff_onboarding_workflows_service.process_paused_license_executions()
            )
            if result.get("resumed", 0) or result.get("skipped", 0):
                log_info(
                    "Staff workflow license resume runner processed executions",
                    **result,
                )

    async def _run_service_status_ai_lookup(self) -> None:
        """Run AI lookups for service status monitors with distributed lock."""
        async with db.acquire_lock(
            "service_status_ai_lookup", timeout=1
        ) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Service status AI lookup already running on another worker, skipping"
                )
                return
            try:
                result = await service_status_service.run_ai_lookup_for_all_services()
                if result.get("checked", 0) or result.get("errors", 0):
                    log_info("Service status AI lookup completed", **result)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Service status AI lookup failed", error=str(exc))

    async def _run_ticket_shipment_watch_runner(self) -> None:
        """Poll due ticket shipment watches with distributed lock."""
        async with db.acquire_lock(
            "ticket_shipment_watch_runner", timeout=1
        ) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Ticket shipment watch runner already running on another worker, skipping"
                )
                return
            try:
                result = await shipment_watch_service.process_due_shipment_watches()
                if result.get("checked", 0) or result.get("errors", 0):
                    log_info("Ticket shipment watch runner completed", **result)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Ticket shipment watch runner failed", error=str(exc))

    async def _run_subscription_renewals(self) -> None:
        """Run subscription renewal invoice creation (T-60 job) with distributed lock."""
        async with db.acquire_lock("subscription_renewals", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Subscription renewals already running on another worker, skipping"
                )
                return

            from datetime import date

            today = date.today()
            log_info("Starting subscription renewal invoice creation", date=today)

            try:
                result = await subscription_renewals.create_renewal_invoices_for_date(
                    today
                )
                log_info("Subscription renewal invoice creation completed", **result)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error(
                    "Subscription renewal invoice creation failed",
                    date=today,
                    error=str(exc),
                )

    async def _run_m365_credential_renewal(self) -> None:
        """Renew expiring Microsoft 365 client secrets with distributed lock."""
        async with db.acquire_lock(
            "m365_credential_renewal", timeout=5
        ) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "M365 credential renewal already running on another worker, skipping"
                )
                return

            log_info("Starting M365 client secret renewal check")
            try:
                result = await m365_service.renew_expiring_client_secrets()
                log_info("M365 client secret renewal check completed", **result)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("M365 client secret renewal check failed", error=str(exc))

    async def _run_huntress_daily_sync(self) -> None:
        """Refresh Huntress snapshots for every linked company."""
        async with db.acquire_lock("huntress_daily_sync", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Huntress daily sync already running on another worker, skipping"
                )
                return
            from app.services import huntress as huntress_service

            try:
                if not await huntress_service.is_module_enabled():
                    log_info("Huntress module disabled; skipping daily sync")
                    return
                result = await huntress_service.refresh_all_companies()
                log_info(
                    "Huntress daily sync completed",
                    status=result.get("status"),
                    refreshed=result.get("refreshed"),
                    skipped=result.get("skipped"),
                    failed=result.get("failed"),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Huntress daily sync failed", error=str(exc))

    async def _run_backup_history_seed(self) -> None:
        """Seed daily 'unknown' backup events with distributed lock."""
        async with db.acquire_lock("backup_history_seed", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Backup history seed already running on another worker, skipping"
                )
                return
            try:
                inserted = await backup_jobs_service.seed_unknown_events_for_date()
                log_info("Backup history seed completed", inserted=inserted)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Backup history seed failed", error=str(exc))

    async def _run_backup_alert_check(self) -> None:
        """Check backup alert thresholds and create tickets with distributed lock."""
        async with db.acquire_lock("backup_alert_check", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Backup alert check already running on another worker, skipping"
                )
                return
            try:
                result = await backup_jobs_service.check_backup_alerts()
                log_info("Backup alert check completed", **result)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Backup alert check failed", error=str(exc))

    async def _run_solidtime_reconcile(self) -> None:
        """Pull Solidtime project / time entry updates with a distributed lock."""
        async with db.acquire_lock("solidtime_reconcile", timeout=5) as lock_acquired:
            if not lock_acquired:
                log_info(
                    "Solidtime reconcile already running on another worker, skipping"
                )
                return
            from app.services import solidtime as solidtime_service

            try:
                if not await solidtime_service.is_module_enabled():
                    return
                result = await solidtime_service.reconcile_once()
                log_info(
                    "Solidtime reconcile completed",
                    status=result.get("status"),
                    projects_pulled=result.get("projects_pulled"),
                    time_entries_pulled=result.get("time_entries_pulled"),
                    errors=result.get("errors"),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Solidtime reconcile failed", error=str(exc))

    def _build_trigger(self, task: dict[str, Any]) -> CronTrigger | None:
        try:
            fields = str(task["cron"]).strip().split()
            if len(fields) != 5:
                raise ValueError(
                    f"Wrong number of fields; got {len(fields)}, expected 5"
                )
            minute, hour, day, month, day_of_week = fields
            return CronTrigger(
                minute=minute,
                hour=hour,
                day=_normalise_cron_day_field(day),
                month=month,
                day_of_week=day_of_week,
                timezone=self._scheduler.timezone,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to parse cron expression",
                task_id=task.get("id"),
                cron=task.get("cron"),
                error=str(exc),
            )
            return None

    async def _run_task(
        self, task: dict[str, Any], *, force_restart: bool = False
    ) -> None:
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
                return

            if not force_restart:
                debounce_cutoff = datetime.now(timezone.utc) - timedelta(seconds=55)
                try:
                    recently_ran = await scheduled_tasks_repo.has_run_since(
                        int(task_id), debounce_cutoff
                    )
                except Exception as exc:  # pragma: no cover - keep scheduler resilient
                    recently_ran = False
                    log_error(
                        "Scheduled task duplicate-run check failed",
                        task_id=task_id,
                        command=command,
                        error=str(exc),
                    )
                if recently_ran:
                    log_info(
                        "Skipping duplicate scheduled task fire",
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
                        await staff_importer.import_contacts_for_company(
                            int(company_id)
                        )
                elif command == "sync_assets":
                    company_id = task.get("company_id")
                    if company_id:
                        await asset_importer.import_assets_for_company(int(company_id))
                elif command == "sync_tactical_assets":
                    company_id = task.get("company_id")
                    if company_id:
                        processed = (
                            await asset_importer.import_tactical_assets_for_company(
                                int(company_id)
                            )
                        )
                        details = json.dumps(
                            {"company_id": int(company_id), "processed": processed},
                            default=str,
                        )
                    else:
                        summary = await asset_importer.import_all_tactical_assets()
                        details = json.dumps(summary, default=str)
                elif command == "push_tactical_companies":
                    summary = await modules_service.push_companies_to_tacticalrmm()
                    details = json.dumps(summary, default=str)
                elif command == "pull_tactical_companies":
                    summary = await modules_service.pull_companies_from_tacticalrmm()
                    details = json.dumps(summary, default=str)
                elif command in {"sync_o365", "sync_m365_data"}:
                    company_id = task.get("company_id")
                    if company_id:
                        company_id_int = int(company_id)
                        licenses_sync_error: str | None = None
                        staff_summary = None
                        staff_sync_error: str | None = None
                        mailboxes_synced = 0
                        mailbox_sync_error: str | None = None
                        try:
                            await m365_service.sync_company_licenses(company_id_int)
                        except Exception as exc:  # noqa: BLE001
                            licenses_sync_error = str(exc)
                        try:
                            staff_summary = (
                                await staff_importer.import_m365_contacts_for_company(
                                    company_id_int
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            staff_sync_error = str(exc)
                        try:
                            mailboxes_synced = await m365_service.sync_mailboxes(
                                company_id_int
                            )
                        except Exception as exc:  # noqa: BLE001
                            mailbox_sync_error = str(exc)
                        details = json.dumps(
                            {
                                "company_id": company_id_int,
                                "licenses_synced": licenses_sync_error is None,
                                "licenses_sync_error": licenses_sync_error,
                                "staff": (
                                    {
                                        "created": staff_summary.created,
                                        "updated": staff_summary.updated,
                                        "skipped": staff_summary.skipped,
                                        "removed": staff_summary.removed,
                                        "total": staff_summary.total,
                                    }
                                    if staff_summary is not None
                                    else None
                                ),
                                "staff_sync_error": staff_sync_error,
                                "mailboxes_synced": mailboxes_synced,
                                "mailbox_sync_error": mailbox_sync_error,
                            },
                            default=str,
                        )
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_m365_licenses":
                    company_id = task.get("company_id")
                    if company_id:
                        company_id_int = int(company_id)
                        try:
                            await m365_service.sync_company_licenses(company_id_int)
                            details = json.dumps(
                                {"company_id": company_id_int, "licenses_synced": True},
                                default=str,
                            )
                        except Exception as exc:  # noqa: BLE001
                            status = "failed"
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "licenses_synced": False,
                                    "error": str(exc)
                                    or f"{type(exc).__name__} (no details)",
                                },
                                default=str,
                            )
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_m365_contacts":
                    company_id = task.get("company_id")
                    if company_id:
                        company_id_int = int(company_id)
                        try:
                            staff_summary = (
                                await staff_importer.import_m365_contacts_for_company(
                                    company_id_int
                                )
                            )
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "staff": (
                                        {
                                            "created": staff_summary.created,
                                            "updated": staff_summary.updated,
                                            "skipped": staff_summary.skipped,
                                            "removed": staff_summary.removed,
                                            "total": staff_summary.total,
                                        }
                                        if staff_summary is not None
                                        else None
                                    ),
                                },
                                default=str,
                            )
                        except Exception as exc:  # noqa: BLE001
                            status = "failed"
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "staff_sync_error": str(exc)
                                    or f"{type(exc).__name__} (no details)",
                                },
                                default=str,
                            )
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_m365_mailboxes":
                    company_id = task.get("company_id")
                    if company_id:
                        company_id_int = int(company_id)
                        try:
                            mailboxes_synced = await m365_service.sync_mailboxes(
                                company_id_int
                            )
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "mailboxes_synced": mailboxes_synced,
                                },
                                default=str,
                            )
                        except Exception as exc:  # noqa: BLE001
                            status = "failed"
                            error_msg = str(exc) or f"{type(exc).__name__} (no details)"
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "mailboxes_synced": 0,
                                    "error": error_msg,
                                },
                                default=str,
                            )
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_m365_email_domains":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await m365_service.sync_email_domains(int(company_id))
                        details = json.dumps(result, default=str)
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "sync_to_xero":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await xero_service.sync_company(int(company_id))
                        if result:
                            details = json.dumps(result, default=str)
                            result_status = (
                                str(
                                    result.get("status")
                                    or result.get("event_status")
                                    or ""
                                )
                                .strip()
                                .lower()
                            )
                            if result_status in {"failed", "error", "partial"}:
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
                        result = await xero_service.sync_company(
                            int(company_id), auto_send=True
                        )
                        if result:
                            details = json.dumps(result, default=str)
                            result_status = (
                                str(
                                    result.get("status")
                                    or result.get("event_status")
                                    or ""
                                )
                                .strip()
                                .lower()
                            )
                            if result_status in {"failed", "error", "partial"}:
                                status = "failed"
                            elif result_status == "skipped":
                                status = "skipped"
                        else:
                            details = None
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "generate_invoice":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await invoice_generator_service.generate_invoice(
                            int(company_id)
                        )
                        if result:
                            details = json.dumps(result, default=str)
                            result_status = (
                                str(result.get("status") or "").strip().lower()
                            )
                            if result_status in {"failed", "error"}:
                                status = "failed"
                            elif result_status == "skipped":
                                status = "skipped"
                        else:
                            details = None
                    else:
                        status = "skipped"
                        details = "Company context required"
                elif command == "unbill_time_entries":
                    company_id = task.get("company_id")
                    result = await unbill_time_entries_service.unbill_time_entries(
                        int(company_id) if company_id else None
                    )
                    if result:
                        details = json.dumps(result, default=str)
                        result_status = str(result.get("status") or "").strip().lower()
                        if result_status == "skipped":
                            status = "skipped"
                elif command == "refresh_company_ids":
                    company_id = task.get("company_id")
                    if company_id:
                        result = await company_id_lookup.lookup_missing_company_ids(
                            int(company_id)
                        )
                        details = json.dumps(result, default=str) if result else None
                    else:
                        result = (
                            await company_id_lookup.refresh_all_missing_company_ids()
                        )
                        details = json.dumps(result, default=str) if result else None
                elif command == "sync_huntress":
                    from app.services import huntress as huntress_service
                    from app.repositories import companies as company_repo

                    company_id = task.get("company_id")
                    if company_id:
                        company = await company_repo.get_company_by_id(int(company_id))
                        if not company:
                            status = "skipped"
                            details = "Company not found"
                        elif not company.get("huntress_organization_id"):
                            status = "skipped"
                            details = "Company is not linked to a Huntress organisation"
                        else:
                            try:
                                result = await huntress_service.refresh_company(company)
                                details = (
                                    json.dumps(result, default=str) if result else None
                                )
                                result_status = str(result.get("status") or "").lower()
                                if result_status == "failed":
                                    status = "failed"
                                elif result_status == "skipped":
                                    status = "skipped"
                            except huntress_service.HuntressConfigurationError as exc:
                                status = "skipped"
                                details = str(exc)
                    else:
                        try:
                            result = await huntress_service.refresh_all_companies()
                            details = (
                                json.dumps(result, default=str) if result else None
                            )
                            result_status = str(result.get("status") or "").lower()
                            if result_status == "skipped":
                                status = "skipped"
                            elif result.get("failed"):
                                # Whole-run is "ok" but individual companies failed; keep ok status
                                pass
                        except huntress_service.HuntressConfigurationError as exc:
                            status = "skipped"
                            details = str(exc)
                elif command == "update_products":
                    await products_service.update_products_from_feed()
                elif command == "update_stock_feed":
                    await products_service.update_stock_feed()
                elif command == "system_update":
                    output = await self.run_system_update(force_restart=force_restart)
                    if output:
                        details = output
                elif command == "update_tray_icon_installer":
                    settings = get_settings()
                    updated_assets = (
                        await tray_installer_service.fetch_latest_tray_installers(
                            repo=settings.github_tray_msi_repo,
                            github_token=settings.github_token,
                        )
                    )
                    details = json.dumps(
                        {
                            "repo": settings.github_tray_msi_repo,
                            "assets": updated_assets,
                            "updated": any(updated_assets.values()),
                        },
                        default=str,
                    )
                elif command == "rag_index_start":
                    active = await rag_index_repo.get_active_job()
                    if active:
                        status = "skipped"
                        details = json.dumps(
                            {
                                "active_job_id": active.get("id"),
                                "active_status": active.get("status"),
                            },
                            default=str,
                        )
                    else:
                        job_id = await rag_index_repo.create_job(source_type="all")
                        await rag_index_repo.update_job(
                            job_id,
                            status="running",
                            message="Indexing started by scheduled task.",
                            started=True,
                        )
                        try:
                            from app.services import agent as agent_service

                            await agent_service.execute_agent_query(
                                "",
                                {"id": 0, "is_super_admin": True},
                                allow_empty_query=True,
                                rag_index_job_id=job_id,
                                cleanup_rag_index=True,
                            )
                            final_status = (
                                "cancelled"
                                if await rag_index_repo.job_stop_requested(job_id)
                                else "completed"
                            )
                            final_message = (
                                "Indexing stopped by a scheduled/admin stop request."
                                if final_status == "cancelled"
                                else "Indexing completed by scheduled task."
                            )
                            await rag_index_repo.update_job(
                                job_id,
                                status=final_status,
                                message=final_message,
                                finished=True,
                            )
                            details = json.dumps(
                                {"job_id": job_id, "status": final_status}, default=str
                            )
                        except Exception as exc:
                            await rag_index_repo.update_job(
                                job_id, status="failed", message=str(exc), finished=True
                            )
                            raise
                elif command == "rag_index_stop":
                    stopped = await rag_index_repo.request_all_active_job_stops()
                    details = json.dumps({"stop_requests": stopped}, default=str)
                elif command == "rag_matching_pause":
                    await rag_relationship_repo.set_matching_paused(True)
                    details = json.dumps({"paused": True}, default=str)
                elif command == "rag_matching_resume":
                    await rag_relationship_repo.set_matching_paused(False)
                    details = json.dumps({"paused": False}, default=str)
                elif command == "rag_cleanup_stale_matches":
                    result = (
                        await rag_relationship_repo.cleanup_stale_matches_and_decisions()
                    )
                    details = json.dumps(result, default=str)
                elif command == "create_scheduled_ticket":
                    # Parse JSON payload from task description
                    task_description = task.get("description") or ""
                    try:
                        payload = (
                            json.loads(task_description) if task_description else {}
                        )
                    except json.JSONDecodeError as exc:
                        status = "failed"
                        details = f"Invalid JSON payload: {str(exc)}"
                        log_error(
                            "Invalid JSON in scheduled ticket creation",
                            task_id=task_id,
                            error=str(exc),
                        )
                    else:
                        company_id = task.get("company_id")
                        render_context: dict[str, Any] = {
                            "schedule": {
                                "task_id": task_id,
                                "task_name": task.get("name"),
                                "command": command,
                            }
                        }
                        if company_id:
                            render_context["company_id"] = company_id
                            render_context["company"] = {"id": company_id}

                        # Render template variables in the payload. Scheduled ticket
                        # payloads include the task company in context so saved
                        # report variables such as {{ report.slug.list }} can
                        # safely use {{current.company}} placeholders.
                        payload = await value_templates.render_value_async(
                            payload, context=render_context
                        )

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
                                    requester_id=(
                                        int(payload.get("requester_id"))
                                        if payload.get("requester_id")
                                        else None
                                    ),
                                    assigned_user_id=(
                                        int(payload.get("assigned_user_id"))
                                        if payload.get("assigned_user_id")
                                        else None
                                    ),
                                    priority=str(payload.get("priority", "normal")),
                                    status=(
                                        str(payload.get("status"))
                                        if payload.get("status")
                                        else None
                                    ),
                                    category=(
                                        str(payload.get("category"))
                                        if payload.get("category")
                                        else None
                                    ),
                                    module_slug=(
                                        str(payload.get("module_slug"))
                                        if payload.get("module_slug")
                                        else None
                                    ),
                                    external_reference=(
                                        str(payload.get("external_reference"))
                                        if payload.get("external_reference")
                                        else None
                                    ),
                                    trigger_automations=False,  # Prevent automation loops
                                )
                                ticket_id = ticket.get("id") if ticket else None
                                ticket_number = ticket.get("number") if ticket else None
                                details = json.dumps(
                                    {
                                        "ticket_id": ticket_id,
                                        "ticket_number": ticket_number,
                                        "subject": subject,
                                    },
                                    default=str,
                                )
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
                elif isinstance(command, str) and command.startswith("m365_mail_sync:"):
                    try:
                        account_id = int(command.split(":", 1)[1])
                    except (IndexError, ValueError):
                        status = "skipped"
                        details = "Invalid M365 mail account reference"
                        log_error(
                            "Invalid M365 mail sync command",
                            task_id=task_id,
                            command=command,
                        )
                    else:
                        from app.services import m365_mail as m365_mail_service

                        result = await m365_mail_service.sync_account(account_id)
                        details = json.dumps(result, default=str) if result else None
                elif command == "send_price_change_notifications":
                    result = (
                        await subscription_price_changes.send_price_change_notifications()
                    )
                    details = json.dumps(result, default=str)
                    log_info("Price change notifications sent", **result)
                elif command == "apply_scheduled_price_changes":
                    result = (
                        await subscription_price_changes.apply_scheduled_price_changes()
                    )
                    details = json.dumps(result, default=str)
                    log_info("Scheduled price changes applied", **result)
                elif command == "sync_recordings":
                    from app.services import call_recordings as call_recordings_service
                    from app.services import modules as modules_service

                    # Get recordings path from module settings
                    module = await modules_service.get_module(
                        "call-recordings", redact=False
                    )
                    if module and module.get("settings"):
                        recordings_path = module["settings"].get("recordings_path")
                        phone_system_type = module["settings"].get("phone_system_type")
                        if recordings_path:
                            result = await call_recordings_service.sync_recordings_from_filesystem(
                                recordings_path,
                                phone_system_type=phone_system_type,
                            )
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

                    result = (
                        await call_recordings_service.queue_pending_transcriptions()
                    )
                    details = json.dumps(result, default=str)
                    log_info("Transcriptions queued", **result)
                elif command == "process_transcription":
                    from app.services import call_recordings as call_recordings_service

                    result = (
                        await call_recordings_service.process_queued_transcriptions()
                    )
                    details = json.dumps(result, default=str)
                    if result.get("status") == "error":
                        status = "failed"
                    log_info("Transcription processed", **result)
                elif command == "sync_unifi_talk_recordings":
                    result = await modules_service.trigger_module(
                        "unifi-talk", {}, background=False
                    )
                    details = json.dumps(result, default=str)
                    module_status = str(result.get("status") or "").lower()
                    if module_status == "error":
                        status = "failed"
                    elif module_status == "skipped":
                        status = "skipped"
                    log_info("Unifi Talk recordings sync completed", **result)
                elif command == "bcp_notify_upcoming_training":
                    # Notify about upcoming BCP training sessions
                    from app.repositories import bcp as bcp_repo
                    from app.repositories import notifications as notifications_repo

                    # Get training items in the next 7 days (configurable via task description)
                    task_description = task.get("description") or ""
                    try:
                        config = (
                            json.loads(task_description) if task_description else {}
                        )
                        days_ahead = config.get("days_ahead", 7)
                    except json.JSONDecodeError:
                        days_ahead = 7

                    upcoming = await bcp_repo.get_upcoming_training_items(
                        days_ahead=days_ahead
                    )

                    if upcoming:
                        for item in upcoming:
                            plan = item.get("plan", {})
                            plan_id = plan.get("id")

                            if plan_id:
                                # Get distribution list for the plan
                                await bcp_repo.list_distribution_list(plan_id)

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
                                        "training_date": item[
                                            "training_date"
                                        ].isoformat(),
                                        "training_type": item.get("training_type"),
                                    },
                                )

                        details = json.dumps(
                            {
                                "upcoming_training_count": len(upcoming),
                                "days_ahead": days_ahead,
                            },
                            default=str,
                        )
                        log_info("BCP training reminders sent", count=len(upcoming))
                    else:
                        status = "skipped"
                        details = f"No upcoming training in next {days_ahead} days"
                        log_info(
                            "No upcoming BCP training to notify", days_ahead=days_ahead
                        )
                elif command == "bcp_notify_upcoming_review":
                    # Notify about upcoming BCP plan reviews
                    from app.repositories import bcp as bcp_repo
                    from app.repositories import notifications as notifications_repo

                    # Get review items in the next 7 days (configurable via task description)
                    task_description = task.get("description") or ""
                    try:
                        config = (
                            json.loads(task_description) if task_description else {}
                        )
                        days_ahead = config.get("days_ahead", 7)
                    except json.JSONDecodeError:
                        days_ahead = 7

                    upcoming = await bcp_repo.get_upcoming_review_items(
                        days_ahead=days_ahead
                    )

                    if upcoming:
                        for item in upcoming:
                            plan = item.get("plan", {})
                            plan_id = plan.get("id")

                            if plan_id:
                                # Get distribution list for the plan
                                _unused_distribution_list = (
                                    await bcp_repo.list_distribution_list(plan_id)
                                )

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
                                    },
                                )

                        details = json.dumps(
                            {
                                "upcoming_review_count": len(upcoming),
                                "days_ahead": days_ahead,
                            },
                            default=str,
                        )
                        log_info("BCP review reminders sent", count=len(upcoming))
                    else:
                        status = "skipped"
                        details = f"No upcoming reviews in next {days_ahead} days"
                        log_info(
                            "No upcoming BCP reviews to notify", days_ahead=days_ahead
                        )
                elif command == "refresh_m365_consent_status":
                    company_id = task.get("company_id")
                    if company_id:
                        company_id_int = int(company_id)
                        try:
                            results = await m365_service.check_enterprise_app_permissions(
                                company_id_int
                            )
                            all_ok = bool(results) and all(
                                app.get("all_ok") for app in results
                            )
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "all_ok": all_ok,
                                    "apps_checked": len(results),
                                },
                                default=str,
                            )
                        except Exception as exc:  # noqa: BLE001
                            status = "failed"
                            details = json.dumps(
                                {
                                    "company_id": company_id_int,
                                    "error": str(exc)
                                    or f"{exc.__class__.__name__} (no details)",
                                },
                                default=str,
                            )
                    else:
                        provisioned_ids = await m365_repo.list_provisioned_company_ids()
                        if not provisioned_ids:
                            status = "skipped"
                            details = "No provisioned M365 companies found"
                        else:
                            company_results: list[dict[str, Any]] = []
                            any_failed = False
                            for cid in provisioned_ids:
                                try:
                                    cid_results = await m365_service.check_enterprise_app_permissions(
                                        cid
                                    )
                                    cid_all_ok = bool(cid_results) and all(
                                        app.get("all_ok", False) for app in cid_results
                                    )
                                    company_results.append(
                                        {
                                            "company_id": cid,
                                            "all_ok": cid_all_ok,
                                            "apps_checked": len(cid_results),
                                        }
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    any_failed = True
                                    company_results.append(
                                        {
                                            "company_id": cid,
                                            "error": str(exc)
                                            or f"{exc.__class__.__name__} (no details)",
                                        }
                                    )
                            if any_failed:
                                status = "failed"
                            details = json.dumps(
                                {
                                    "companies_checked": len(provisioned_ids),
                                    "results": company_results,
                                },
                                default=str,
                            )
                else:
                    status = "skipped"
                    details = "No handler registered for command"
                    log_info(
                        "Scheduled task has no handler",
                        task_id=task_id,
                        command=command,
                    )
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

    async def run_system_update(self, *, force_restart: bool = False) -> str | None:
        """Public helper to execute the system update script.

        This wraps the private implementation so that other parts of the
        application can reuse the same update mechanism used by scheduled
        tasks.
        """
        return await self._run_system_update(force_restart=force_restart)

    async def _run_system_update(self, *, force_restart: bool = False) -> str | None:
        async with _SYSTEM_UPDATE_LOCK:
            local_head = await self._get_git_ref("HEAD")
            remote_head = await self._get_remote_main_ref()
            if not local_head or not remote_head:
                raise RuntimeError(
                    "Unable to determine local and remote Git refs for system update"
                )

            if local_head == remote_head:
                message = "No GitHub update available; upgrade was not scheduled."
                log_info(
                    "System update skipped",
                    reason="already_up_to_date",
                    local_head=local_head,
                    remote_head=remote_head,
                    requested_from_ui=force_restart,
                )
                return message

            requested_mode = self._resolve_requested_upgrade_mode(
                force_restart=force_restart
            )
            changed_files: list[str] | None = None
            if not force_restart:
                fetched_head = await self._fetch_remote_main_ref()
                if fetched_head:
                    changed_files = await self._list_changed_files(
                        local_head, fetched_head
                    )

            # Try to apply the update via the in-process feature-pack
            # reload API when the incoming diff is limited to one or
            # more feature packs whose ``PACK.version`` has been bumped.
            # This avoids dropping connections for routine pack-only
            # updates.  Any failure or ambiguity falls through to the
            # full-restart flag-file path below.
            if not force_restart:
                hot_reload_message = await self._try_feature_pack_hot_reload(
                    local_head=local_head,
                    remote_head=remote_head,
                )
                if hot_reload_message is not None:
                    return hot_reload_message

            self._ensure_update_flag_directory()
            timestamp = datetime.now(timezone.utc).isoformat()
            requested_reason = (
                "manual_restart_requested"
                if force_restart
                else self._classify_full_upgrade_reason(changed_files)
            )
            flag_payload = (
                f"requested_at={timestamp}\n"
                f"requested_from_ui={str(force_restart).lower()}\n"
                f"requested_mode={requested_mode}\n"
                f"requested_reason={requested_reason}\n"
                f"local_head={local_head}\n"
                f"remote_head={remote_head}\n"
            )
            _SYSTEM_UPDATE_FLAG_PATH.write_text(flag_payload, encoding="utf-8")
            os.chmod(_SYSTEM_UPDATE_FLAG_PATH, 0o640)

            log_info(
                "System update scheduled",
                flag=str(_SYSTEM_UPDATE_FLAG_PATH),
                local_head=local_head,
                remote_head=remote_head,
                requested_mode=requested_mode,
                requested_reason=requested_reason,
                requested_from_ui=force_restart,
            )
            return (
                f"Update scheduled via {_SYSTEM_UPDATE_FLAG_PATH.name} "
                f"using {requested_mode} mode."
            )

    async def _try_feature_pack_hot_reload(
        self, *, local_head: str, remote_head: str
    ) -> str | None:
        """Attempt to apply the pending update via feature-pack hot reload.

        Returns a human-readable success message when every changed file
        belongs to a currently-loaded feature pack whose ``PACK.version``
        literal has been bumped in the incoming code, and every affected
        pack reloads cleanly.  Returns ``None`` to indicate the caller
        should fall back to the full-restart flag-file path.
        """

        try:
            from app.core.features import get_registry
        except Exception:  # pragma: no cover - defensive
            return None
        try:
            registry = get_registry()
        except RuntimeError:
            # Registry not initialised (e.g. tests, very early startup);
            # fall back to the standard restart path.
            return None

        fetched_head = await self._fetch_remote_main_ref()
        if not fetched_head:
            return None

        changed_files = await self._list_changed_files(local_head, fetched_head)
        if changed_files is None or not changed_files:
            return None

        slugs = self._classify_feature_pack_changes(changed_files)
        if slugs is None:
            log_info(
                "System update requires full restart",
                reason="changes_outside_feature_packs",
                changed_file_count=len(changed_files),
            )
            return None

        loaded_versions: dict[str, str] = {
            state["slug"]: state["version"] for state in registry.list()
        }
        for slug in slugs:
            if slug not in loaded_versions:
                log_info(
                    "Feature pack hot-reload skipped",
                    reason="pack_not_loaded",
                    slug=slug,
                )
                return None

        # The ``PACK.version`` literal is no longer used as a gate for
        # hot-reload — pack-only file diffs are already proof that the
        # code changed, and requiring a manual version bump in every
        # edit was unreliable in practice (forgotten bumps silently
        # fell back to full restarts).  We still read the incoming
        # version literal for logging, and we still skip hot-reload if
        # the incoming ``__init__.py`` is missing entirely (which
        # implies the pack was removed/renamed and a restart is
        # needed).
        incoming_versions: dict[str, str] = {}
        for slug in sorted(slugs):
            new_version = await self._read_pack_version_at_ref(slug, fetched_head)
            if not new_version:
                log_info(
                    "Feature pack hot-reload skipped",
                    reason="missing_incoming_version",
                    slug=slug,
                )
                return None
            incoming_versions[slug] = new_version
            if new_version == loaded_versions[slug]:
                log_info(
                    "Feature pack hot-reload proceeding without version bump",
                    slug=slug,
                    version=new_version,
                )

        # Fast-forward the working tree so the new pack code is on disk
        # before we ask the registry to re-import it.  ``--ff-only``
        # refuses to create a merge commit, matching the upgrade
        # script's expectation that ``main`` advances linearly.
        rc, _, stderr = await self._run_git("merge", "--ff-only", fetched_head)
        if rc != 0:
            log_error(
                "Feature pack hot-reload aborted: fast-forward merge failed",
                error=_truncate_output(stderr),
            )
            return None

        reloaded: list[str] = []
        for slug in sorted(slugs):
            try:
                state = await registry.reload(slug)
            except Exception as exc:
                log_error(
                    "Feature pack hot-reload failed; falling back to full restart",
                    slug=slug,
                    error=str(exc),
                )
                return None
            if state.last_error:
                log_error(
                    "Feature pack hot-reload reported an error; falling back to full restart",
                    slug=slug,
                    error=state.last_error,
                )
                return None
            reloaded.append(slug)

        log_info(
            "Feature pack hot-reload completed",
            packs=reloaded,
            local_head=local_head,
            remote_head=remote_head,
        )
        summary = ", ".join(f"{slug}@{incoming_versions[slug]}" for slug in reloaded)
        return f"Feature pack(s) reloaded without restart: {summary}."

    async def _consume_feature_pack_reload_flag(self) -> None:
        """Reload feature packs listed in ``_FEATURE_PACK_RELOAD_FLAG_PATH``.

        ``scripts/upgrade.sh`` writes this flag (one slug per line) when it
        pulls a diff that is fully scoped to ``app/features/<slug>/`` so
        the running app can pick up the on-disk code without a service
        restart.  The flag is deleted once every listed pack has been
        reloaded successfully; if any reload fails the flag is left in
        place so the next tick can retry the remaining packs.
        """

        try:
            raw = _FEATURE_PACK_RELOAD_FLAG_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            log_error(
                "Failed to read feature pack reload flag",
                path=str(_FEATURE_PACK_RELOAD_FLAG_PATH),
                error=str(exc),
            )
            return

        slugs: list[str] = []
        seen: set[str] = set()
        for raw_line in raw.splitlines():
            slug = raw_line.strip()
            if not slug or slug.startswith("#"):
                continue
            if slug in seen:
                continue
            seen.add(slug)
            slugs.append(slug)

        if not slugs:
            try:
                _FEATURE_PACK_RELOAD_FLAG_PATH.unlink()
            except OSError:
                pass
            return

        try:
            from app.core.features import get_registry
        except Exception:  # pragma: no cover - defensive
            return
        try:
            registry = get_registry()
        except RuntimeError:
            # Registry not initialised yet; try again on the next tick.
            return

        loaded = {state["slug"] for state in registry.list()}
        reloaded: list[str] = []
        failed: list[str] = []
        for slug in slugs:
            if slug not in loaded:
                log_info(
                    "Feature pack reload flag skipped slug",
                    reason="pack_not_loaded",
                    slug=slug,
                )
                failed.append(slug)
                continue
            try:
                state = await registry.reload(slug)
            except Exception as exc:
                log_error(
                    "Feature pack reload flag handler failed",
                    slug=slug,
                    error=str(exc),
                )
                failed.append(slug)
                continue
            if state.last_error:
                log_error(
                    "Feature pack reload flag handler reported error",
                    slug=slug,
                    error=state.last_error,
                )
                failed.append(slug)
                continue
            reloaded.append(slug)

        if reloaded:
            log_info(
                "Feature pack reload flag applied",
                packs=reloaded,
                failed=failed,
            )

        if not failed:
            try:
                _FEATURE_PACK_RELOAD_FLAG_PATH.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log_error(
                    "Failed to clear feature pack reload flag",
                    path=str(_FEATURE_PACK_RELOAD_FLAG_PATH),
                    error=str(exc),
                )
        else:
            # Leave only the failed slugs in the flag so the next tick
            # retries them.  Successful reloads are dropped.
            try:
                _FEATURE_PACK_RELOAD_FLAG_PATH.write_text(
                    "\n".join(failed) + "\n", encoding="utf-8"
                )
            except OSError as exc:  # pragma: no cover - defensive
                log_error(
                    "Failed to write remaining slugs to feature pack reload flag",
                    path=str(_FEATURE_PACK_RELOAD_FLAG_PATH),
                    error=str(exc),
                )

    @staticmethod
    def _classify_feature_pack_changes(changed_files: list[str]) -> set[str] | None:
        """Return the affected slugs when every file is feature-pack scoped.

        Returns ``None`` if any changed file lives outside
        ``app/features/<slug>/`` (including files directly in
        ``app/features/`` itself, such as ``app/features/__init__.py``),
        because such changes require restarting the host application.
        """

        slugs: set[str] = set()
        for raw in changed_files:
            path = raw.strip()
            if not path:
                continue
            if not path.startswith(_FEATURE_PACKS_DIR_PREFIX):
                return None
            rest = path[len(_FEATURE_PACKS_DIR_PREFIX) :]
            parts = rest.split("/", 1)
            if len(parts) < 2 or not parts[0]:
                # File sits directly under ``app/features/`` (e.g. the
                # package ``__init__.py``); not a per-pack file.
                return None
            slugs.add(parts[0])
        return slugs or None

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=PIPE,
            stderr=PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        stdout, stderr = await process.communicate()
        return (
            int(process.returncode or 0),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _fetch_remote_main_ref(self) -> str | None:
        rc, _, stderr = await self._run_git("fetch", "origin", "main")
        if rc != 0:
            log_error(
                "Failed to fetch origin/main for feature pack diff",
                error=_truncate_output(stderr),
            )
            return None
        rc, stdout, stderr = await self._run_git("rev-parse", "FETCH_HEAD")
        if rc != 0:
            log_error(
                "Failed to resolve FETCH_HEAD after fetching origin/main",
                error=_truncate_output(stderr),
            )
            return None
        ref = stdout.strip()
        return ref or None

    async def _list_changed_files(self, base: str, head: str) -> list[str] | None:
        rc, stdout, stderr = await self._run_git(
            "diff", "--name-only", f"{base}..{head}"
        )
        if rc != 0:
            log_error(
                "Failed to diff local HEAD against incoming refs",
                error=_truncate_output(stderr),
            )
            return None
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    async def _read_pack_version_at_ref(self, slug: str, ref: str) -> str | None:
        rc, stdout, _ = await self._run_git(
            "show", f"{ref}:{_FEATURE_PACKS_DIR_PREFIX}{slug}/__init__.py"
        )
        if rc != 0:
            return None
        match = _PACK_VERSION_RE.search(stdout)
        return match.group(1) if match else None

    @staticmethod
    def _resolve_requested_upgrade_mode(*, force_restart: bool = False) -> str:
        if force_restart:
            return "restart"
        return _normalise_upgrade_mode(os.getenv("APP_UPGRADE_MODE"))

    @staticmethod
    def _classify_full_upgrade_reason(changed_files: list[str] | None) -> str:
        if not changed_files:
            return "application_reload_required"

        stripped = [path.strip() for path in changed_files if path and path.strip()]
        if not stripped:
            return "application_reload_required"

        if any(path == "pyproject.toml" for path in stripped):
            return "dependency_manifest_changed"
        if any(path.startswith("migrations/") for path in stripped):
            return "migrations_changed"
        if any(path.startswith("deploy/") for path in stripped):
            return "deployment_topology_changed"
        if any(path.startswith("scripts/") for path in stripped):
            return "upgrade_runtime_changed"
        if any(path.startswith("app/") for path in stripped):
            return "shared_app_code_changed"
        return "application_reload_required"

    def _ensure_update_flag_directory(self) -> None:
        _SYSTEM_UPDATE_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(_SYSTEM_UPDATE_FLAG_PATH.parent, 0o700)
        except OSError:
            # Best-effort permission hardening; failures are non-fatal for scheduling.
            pass

    async def _get_git_ref(self, ref: str) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            ref,
            stdout=PIPE,
            stderr=PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            stderr_preview = _truncate_output(stderr)
            log_error("Failed to resolve Git ref", ref=ref, error=stderr_preview)
            return None
        return _truncate_output(stdout)

    async def _get_remote_main_ref(self) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "ls-remote",
            "--heads",
            "origin",
            "main",
            stdout=PIPE,
            stderr=PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            stderr_preview = _truncate_output(stderr)
            log_error(
                "Failed to query GitHub for latest main ref", error=stderr_preview
            )
            return None
        response = _truncate_output(stdout)
        if not response:
            return None
        first_line = response.splitlines()[0]
        return first_line.split()[0] if first_line.split() else None


scheduler_service = SchedulerService()
