"""Health check feature pack.

An AI health check of the computer behind an asset, run through its tray
device with the AI troubleshooter's settings, and the review page for logs
the AI agents need but cannot read. Every computer with the RMM agent also
runs one a month at a random day and time when it matches a monthly check
group's device rules (:mod:`app.services.ai_health_check_schedule`), and the Detections page lists
what the automatic checks can find, the event log rules that flag it as it
happens and the scripts admins link to fix it
(:mod:`app.services.health_check_detections`,
:mod:`app.services.health_check_remediation`). See
:mod:`app.services.ai_health_check`, :mod:`app.services.ai_required_logs`,
:mod:`.routes`, :mod:`.detection_routes` and :mod:`.schedule_routes`.

Disable it with ``DISABLED_FEATURE_PACKS=health_check``: the menu entries,
the asset page's Health check section, the monthly checks and every route go
away. Reports, schedules, linked scripts and required logs stay in the
database, and the AI troubleshooter keeps recording the logs it could not read.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.services import ai_health_check_schedule, rmm_automation

from .detection_routes import router as detection_router
from .routes import router
from .schedule_routes import router as schedule_router

# Monthly health checks start on the RMM automation tick.
rmm_automation.register_tick_job(ai_health_check_schedule.tick)

PACK = FeaturePack(
    slug="health_check",
    version="1.1.0",
    routers=(detection_router, schedule_router, router),
)

__all__ = ["PACK"]
