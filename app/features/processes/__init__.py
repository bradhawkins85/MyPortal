"""Reusable, versioned process templates and immutable execution runs."""

from app.core.features import FeaturePack

from .routes import router

PACK = FeaturePack(slug="processes", version="1.0.0", routers=(router,))

__all__ = ["PACK"]
