"""RMM feature pack: scripts loaded from Gitea and run on devices by the RMM agent.

* ``GET /rmm/scripts`` script library, devices with an RMM agent and run history
* ``/api/rmm/...`` technician APIs for script fields, pushing runs and results
* ``/api/rmm/agent/...`` the API the separate RMM agent uses to enrol, collect
  runs and report exit codes, output and custom values
* ``GET /rmm/automation`` scheduled scripts and the onboarding scripts run
  when a device's RMM agent first enrols (:mod:`app.services.rmm_automation`)
* ``GET /rmm/remote-control`` RustDesk and MeshCentral, switched on per session
  by an activation script (:mod:`app.services.rmm_remote_control`)
* ``GET /rmm/event-logs`` Windows event logs agents send to the separate event
  log database, and the extra logs collected per company and device
  (:mod:`app.services.rmm_event_logs`)
* ``GET /rmm/event-logs/rules`` rules that drop incoming event log entries or
  run an RMM script on the device that sent them
  (:mod:`app.services.rmm_event_log_rules`)
* ``GET /rmm/checks`` checks for disk space, ping, CPU, memory, services,
  network throughput, script results and agents that stop checking in, with
  the same actions as event log rules; the agent reports what they need and
  device stats go to the separate stats database (:mod:`app.services.rmm_checks`)
* each asset page's Hardware & software section: the hardware and installed
  software the RMM agent reports daily, on start and when asked to refresh,
  ready for filtering which devices scripts run on
  (:mod:`app.services.rmm_inventory`)
* ``POST /api/rmm/assets/{id}/agent-install`` push the RMM agent to a device
  through its tray app, and the tray app's download and result APIs
  (:mod:`app.services.rmm_agent_deploy`)
* ``GET /rmm/findings`` (super admins) a platform-wide, best-guess look at
  every company's RMM data for abnormalities: event spikes, lockouts, new
  accounts and services, software changes and slow devices, from fixed rules
  and the configured language model (:mod:`app.services.rmm_findings`)

A background job keeps the Gitea repository's ``Common/`` and
``Companies/<company>/`` folders in place, adding a folder for each new
company, and another runs due schedules and moves onboarding sequences on
and runs server-side checks (each on one instance at a time; see
:mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=rmm``: the menu entry, the asset
page's script card and every route above go away. Scripts, agents, schedules,
onboarding steps, remote control settings and run history stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from app.services import rmm_automation, rmm_checks, rmm_event_log_rules, rmm_findings, rmm_scripts

from .agent_deploy_routes import router as agent_deploy_router
from .automation_routes import router as automation_router
from .check_routes import router as check_router
from .event_log_routes import router as event_log_router
from .event_log_rule_routes import router as event_log_rule_router
from .finding_routes import router as finding_router
from .inventory_routes import router as inventory_router
from .remote_control_routes import router as remote_control_router
from .routes import router

# Event log rules that schedule a script are queued on the automation tick,
# as are checks judged on the server (agent offline, script results) and
# check actions scheduled for later.
rmm_automation.register_tick_job(rmm_event_log_rules.run_due_scheduled)
rmm_automation.register_tick_job(rmm_checks.tick)
# Findings analyses start on the tick when one is due and run in the background.
rmm_automation.register_tick_job(rmm_findings.tick)

PACK = FeaturePack(
    slug="rmm",
    version="1.15.0",
    routers=(router, automation_router, remote_control_router, agent_deploy_router, event_log_router,
             event_log_rule_router, inventory_router, check_router, finding_router),
    background_jobs=(rmm_scripts.folder_maintenance_loop, rmm_automation.automation_loop),
)

__all__ = ["PACK"]
