"""Backups feature pack.

Owns the admin backup job page routes:

* ``GET  /admin/backup-jobs``
* ``HEAD /admin/backup-jobs``
* ``GET  /admin/backup-summary``
* ``HEAD /admin/backup-summary``
* ``POST /admin/backup-jobs``
* ``POST /admin/backup-jobs/{job_id}``
* ``POST /admin/backup-jobs/{job_id}/delete``
* ``POST /admin/backup-jobs/{job_id}/regenerate-token``

and the company backup register under Assets & Network (``register.py``):

* ``GET  /backups`` lists tracked jobs and manual backup entries
* ``GET  /backups/new`` and ``POST /backups``
* ``GET  /backups/{entry_id}/edit`` and ``POST /backups/{entry_id}``
* ``POST /backups/{entry_id}/delete``
* ``GET  /backups/tracked/{job_id}/edit`` and ``POST /backups/tracked/{job_id}``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently. The backup status webhook API route under
``app/api/routes/backup_jobs.py`` remains mounted directly by
``app/main.py``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .register import router as register_router
from .routes import router as backups_router


PACK = FeaturePack(
    slug="backups",
    version="1.0.0",
    routers=(backups_router, register_router),
)


__all__ = ["PACK"]
