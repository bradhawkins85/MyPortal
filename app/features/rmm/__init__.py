"""RMM feature pack: scripts loaded from Gitea and run on devices by the RMM agent.

* ``GET /rmm/scripts`` script library, devices with an RMM agent and run history
* ``/api/rmm/...`` technician APIs for script fields, pushing runs and results
* ``/api/rmm/agent/...`` the API the separate RMM agent uses to enrol, collect
  runs and report exit codes, output and custom values
* ``GET /rmm/automation`` scheduled scripts and the onboarding scripts run
  when a device's RMM agent first enrols (:mod:`app.services.rmm_automation`)
* ``GET /rmm/remote-control`` RustDesk and MeshCentral, switched on per session
  by an activation script (:mod:`app.services.rmm_remote_control`)

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

from app.services import rmm_automation, rmm_scripts

from .automation_routes import router as automation_router
from .remote_control_routes import router as remote_control_router
from .routes import router

PACK = FeaturePack(
    slug="rmm",
    version="1.3.0",
    routers=(router, automation_router, remote_control_router),
    background_jobs=(rmm_scripts.folder_maintenance_loop, rmm_automation.automation_loop),
)

__all__ = ["PACK"]
