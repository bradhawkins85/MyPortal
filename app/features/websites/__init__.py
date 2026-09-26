"""Company website documentation and safe monitoring."""
from app.core.features import FeaturePack
from .routes import router, web_router

PACK = FeaturePack(slug="websites", version="1.1.0", routers=(router, web_router))

__all__ = ["PACK"]
