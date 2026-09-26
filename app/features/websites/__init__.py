"""Company website documentation and safe monitoring."""
from app.core.features import FeaturePack
from .routes import router

PACK = FeaturePack(slug="websites", version="1.0.0", routers=(router,))

__all__ = ["PACK"]
