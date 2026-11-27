"""Project management feature pack (:mod:`app.services.projects`).

* ``GET /admin/projects`` open (or all) projects with progress, hours against
  budget, due dates and billing; the project editor
* ``GET /admin/projects/{id}`` a project: phases with their task tickets,
  milestones, and the value of the time logged against the budget or fee
* ``POST`` routes under ``/admin/projects/{id}/`` for phases, tasks (new or
  existing tickets) and milestones; super admins invoice milestones
* ``/api/projects`` list, read, create, update and delete projects, add
  tasks and invoice milestones

Time on fixed-fee project tasks is left off ticket invoices by the invoice
generator and the Xero ticket sync, even if this pack is disabled.

Disable it with ``DISABLED_FEATURE_PACKS=projects``. Projects stay in the
database and their tickets are untouched.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router

PACK = FeaturePack(
    slug="projects",
    version="1.0.1",
    routers=(router,),
)

__all__ = ["PACK"]
