"""Technician scheduling and dispatch feature pack (:mod:`app.services.scheduling`).

* ``GET /admin/schedule`` the dispatch board: technicians across the working
  day, tickets waiting to be scheduled, and the booking editor
* ``GET /admin/schedule/me`` a technician's week
* ``POST /admin/schedule/appointments`` (and ``/{id}``, ``/{id}/status``,
  ``/{id}/sync``) book, move, complete, cancel and re-sync appointments
* ``POST /admin/schedule/settings`` (super admins) calendar sync, time zone,
  working hours and default length
* ``/api/appointments`` (``/{id}/move`` backs dragging on the board) and
  ``/api/tickets/{id}/appointments``
* each ticket page's Appointments card

A background job retries Microsoft 365 calendar writes that failed (on one
instance at a time; see :mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=scheduling``. Appointments stay in
the database and in technicians' calendars.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.services import scheduling

from .routes import router

PACK = FeaturePack(
    slug="scheduling",
    version="1.1.0",
    routers=(router,),
    background_jobs=(scheduling.sync_job,),
)

__all__ = ["PACK"]
