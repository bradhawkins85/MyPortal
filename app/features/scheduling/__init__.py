"""Technician scheduling and dispatch feature pack (:mod:`app.services.scheduling`).

* ``GET /admin/schedule`` the dispatch board: technicians across the working
  day, tickets waiting to be scheduled, and the booking editor
* ``GET /admin/schedule/me`` a technician's week
* ``POST /admin/schedule/appointments`` (and ``/{id}``, ``/{id}/status``,
  ``/{id}/sync``) book, move, complete, cancel and re-sync appointments
* ``POST /admin/schedule/settings`` (super admins) calendar sync, time zone,
  working hours and default length
* ``POST /admin/schedule/kinds`` (and ``/{id}``, ``/{id}/default``,
  ``/{id}/delete``; super admins) add, rename, remove and choose the default
  booking kind, from the schedule page's Actions menu
* ``/api/appointments``, ``/api/tickets/{id}/appointments`` and
  ``/api/scheduling/kinds``
* ``GET /api/scheduling/availability`` a technician's taken time on a day
* each ticket page's Appointments card, which books in place and returns to
  the ticket

A background job retries Microsoft 365 calendar writes that failed and imports
technicians' busy time (only when, never what) from their calendars, on one
instance at a time (see :mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=scheduling``. Appointments stay in
the database and in technicians' calendars.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.services import scheduling

from .routes import router

PACK = FeaturePack(
    slug="scheduling",
    version="1.2.1",
    routers=(router,),
    background_jobs=(scheduling.sync_job,),
)

__all__ = ["PACK"]
