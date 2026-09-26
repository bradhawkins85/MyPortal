"""Reusable, versioned process templates and immutable execution runs."""

from app.core.features import FeaturePack

from .routes import router, web_router

PACK = FeaturePack(slug="processes", version="1.1.0", routers=(router, web_router))

__all__ = ["PACK"]
