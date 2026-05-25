"""Ollama feature pack.

Owns Ollama MCP API routes:

* ``POST /api/mcp/ollama/``
* ``POST /api/mcp/ollama/rpc``
* ``GET /api/mcp/ollama/manifest``
"""

from __future__ import annotations

from app.api.routes.mcp import ollama_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="ollama",
    version="1.0.0",
    routers=(ollama_router,),
)


__all__ = ["PACK"]
