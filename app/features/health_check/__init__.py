"""Health check feature pack.

An AI health check of the computer behind an asset, run through its tray
device with the AI troubleshooter's settings, and the review page for logs
the AI agents need but cannot read. See :mod:`app.services.ai_health_check`,
:mod:`app.services.ai_required_logs` and :mod:`.routes`.

Disable it with ``DISABLED_FEATURE_PACKS=health_check``: the menu entries,
the asset page's Health check section and every route go away. Reports and
required logs stay in the database, and the AI troubleshooter keeps
recording the logs it could not read.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router

PACK = FeaturePack(
    slug="health_check",
    version="1.0.0",
    routers=(router,),
)

__all__ = ["PACK"]
