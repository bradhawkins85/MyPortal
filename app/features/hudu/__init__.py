"""Hudu feature pack.

This pack makes the Hudu integration a first-class hot-reloadable
feature-pack unit. Credentials are managed through the standard
``integration_modules`` settings for the ``hudu`` module, so no
standalone routes are required in this pack.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="hudu",
    version="1.0.0",
)


__all__ = ["PACK"]
