"""Voice-monitor customer and administration feature pack."""

from app.core.features import FeaturePack
from .customer_routes import router as customer_router
from .admin_routes import router as admin_router

PACK = FeaturePack(
    slug="voice_monitor",
    version="1.0.0",
    routers=(customer_router, admin_router),
    description="Tenant-safe voice monitoring management and diagnostics.",
)
__all__ = ["PACK"]
