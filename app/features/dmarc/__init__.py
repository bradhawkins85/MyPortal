"""DMARC reporting feature pack."""
from app.core.features import FeaturePack
from .routes import router

PACK = FeaturePack(slug="dmarc", version="1.0.0", routers=(router,))
__all__ = ["PACK"]
