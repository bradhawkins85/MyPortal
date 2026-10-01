"""Compliance feature pack.

Owns the compliance dashboard/routes:

* ``GET /compliance`` (SMB1001 dashboard)
* ``GET /compliance/attestation-report.pdf`` (SMB1001 attestation export)
* ``POST /compliance/smb1001/{control_id}/ticket``
* ``GET /compliance/essential8`` (legacy Essential 8 overview)
* ``GET /compliance/control/{control_id}`` (legacy Essential 8 control)
* ``POST /compliance/requirements/{requirement_id}/ticket``
* ``GET /compliance-checks``
* ``GET /compliance-checks/{assignment_id}``
* ``GET /admin/compliance-checks/library``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as compliance_router


PACK = FeaturePack(
    slug="compliance",
    version="1.3.0",
    routers=(compliance_router,),
)


__all__ = ["PACK"]
