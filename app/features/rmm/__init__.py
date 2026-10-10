"""RMM feature pack: scripts loaded from Gitea and run on devices by the RMM agent.

* ``GET /rmm/scripts`` script library, devices with an RMM agent and run history
* ``/api/rmm/...`` technician APIs for script fields, pushing runs and results
* ``/api/rmm/agent/...`` the API the separate RMM agent uses to enrol, collect
  runs and report exit codes, output and custom values

A background job keeps the Gitea repository's ``Common/`` and
``Companies/<company>/`` folders in place, adding a folder for each new
company (one instance at a time; see :mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=rmm``: the menu entry, the asset
page's script card and every route above go away. Scripts, agents and run
history stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from app.services import rmm_scripts

from .routes import router

PACK = FeaturePack(
    slug="rmm",
    version="1.1.0",
    routers=(router,),
    background_jobs=(rmm_scripts.folder_maintenance_loop,),
)

__all__ = ["PACK"]
