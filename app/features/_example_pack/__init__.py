"""Reference feature pack used by the loader tests.

This pack is intentionally tiny.  It exists so the feature-pack
machinery has something real to mount during ``pytest`` and so future
pack authors have a minimal worked example to copy.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.features import FeaturePack


router = APIRouter(prefix="/_example", tags=["_example"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"pong": "v1"}


PACK = FeaturePack(
    slug="_example_pack",
    version="1.0.0",
    routers=(router,),
)
