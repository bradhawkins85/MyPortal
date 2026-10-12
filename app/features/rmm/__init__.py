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
* each asset page's Hardware & software section: the hardware and installed
  software the RMM agent reports daily, on start and when asked to refresh,
  ready for filtering which devices scripts run on
  (:mod:`app.services.rmm_inventory`)
* ``POST /api/rmm/assets/{id}/agent-install`` push the RMM agent to a device
  through its tray app, and the tray app's download and result APIs
  (:mod:`app.services.rmm_agent_deploy`)

A background job keeps the Gitea repository's ``Common/`` and
``Companies/<company>/`` folders in place, adding a folder for each new
company, and another runs due schedules and moves onboarding sequences on
(each on one instance at a time; see :mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=rmm``: the menu entry, the asset
page's script card and every route above go away. Scripts, agents, schedules,
onboarding steps, remote control settings and run history stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from app.services import rmm_automation, rmm_event_log_rules, rmm_scripts

from .agent_deploy_routes import router as agent_deploy_router
from .automation_routes import router as automation_router
from .event_log_routes import router as event_log_router
from .event_log_rule_routes import router as event_log_rule_router
from .inventory_routes import router as inventory_router
from .remote_control_routes import router as remote_control_router
from .routes import router

# Event log rules that schedule a script are queued on the automation tick.
rmm_automation.register_tick_job(rmm_event_log_rules.run_due_scheduled)

PACK = FeaturePack(
    slug="rmm",
    version="1.9.0",
    routers=(router, automation_router, remote_control_router, agent_deploy_router, event_log_router,
             event_log_rule_router, inventory_router),
    background_jobs=(rmm_scripts.folder_maintenance_loop, rmm_automation.automation_loop),
)

__all__ = ["PACK"]
