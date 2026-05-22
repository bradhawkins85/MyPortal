"""Reprocess AI feature pack.

This pack makes the ``reprocess-ai`` automation action a first-class
hot-reloadable feature-pack unit.  The action re-triggers Ollama AI
processing (summary and/or tag generation) for a ticket and is already
handled by ``app.services.modules._invoke_reprocess_ai``, so no
standalone routes are required in this pack.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="reprocess_ai",
    version="1.0.0",
)


__all__ = ["PACK"]
