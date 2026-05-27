"""ChatGPT MCP feature pack.

Owns the ChatGPT MCP API route:

* ``POST /api/mcp/chatgpt/``
"""

from __future__ import annotations

from app.api.routes.mcp import chatgpt_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="chatgpt_mcp",
    version="1.0.0",
    routers=(chatgpt_router,),
)


__all__ = ["PACK"]
