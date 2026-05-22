"""Password Pusher feature pack.

This pack currently exists to make the Password Pusher integration a
first-class hot-reloadable feature-pack unit, even though the
integration does not yet own any standalone routes.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="password_pusher",
    version="1.0.0",
)


__all__ = ["PACK"]
