"""Help feature pack.

Owns the ``GET /help`` and ``GET /help/{section}/{article}`` routes that
display the contents of the ``docs/wiki`` folder, organised into sections
based on subfolders.

Files within each subfolder are dynamically loaded at request time, so
new articles become available without restarting the application.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as help_router


PACK = FeaturePack(
    slug="help",
    version="1.0.0",
    routers=(help_router,),
)


__all__ = ["PACK"]
