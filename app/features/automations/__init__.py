"""Automations feature pack.

Owns the admin automations page routes:

* ``GET /admin/automations``
* ``GET /admin/automations/create/scheduled``
* ``GET /admin/automations/create/event``
* ``POST /admin/automations``
* ``GET /admin/automations/{automation_id}/edit``
* ``POST /admin/automations/{automation_id}``
* ``POST /admin/automations/{automation_id}/status``
* ``POST /admin/automations/{automation_id}/execute``
* ``POST /admin/automations/{automation_id}/clone``
* ``GET /admin/automations/{automation_id}/export``
* ``POST /admin/automations/import``
* ``POST /admin/automations/{automation_id}/delete``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as automations_router


PACK = FeaturePack(
    slug="automations",
    version="1.3.0",
    routers=(automations_router,),
)


__all__ = ["PACK"]
