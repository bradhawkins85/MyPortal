"""Huntress feature pack.

This pack makes the Huntress integration a first-class hot-reloadable
feature-pack unit.  Credentials are read from the environment
(``HUNTRESS_API_KEY`` / ``HUNTRESS_API_SECRET`` / ``HUNTRESS_BASE_URL``)
plus Curricula Managed SAT credentials (``CURRICULA_API_KEY`` /
``CURRICULA_API_SECRET`` / ``CURRICULA_BASE_URL``), and the module is gated
by the standard ``integration_modules`` enable/disable toggle — so no standalone
routes are needed here.

The daily sync is triggered by the scheduler (``sync_huntress`` command).
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="huntress",
    version="1.0.0",
)


__all__ = ["PACK"]
