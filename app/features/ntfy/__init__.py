"""ntfy feature pack.

This pack makes the ntfy integration a first-class hot-reloadable
feature-pack unit.  Credentials (``base_url``, ``topic``,
``auth_token``) are managed through the standard
``integration_modules`` settings for the ``ntfy`` module, so no
standalone routes are required in this pack.

The ``_invoke_ntfy`` handler in ``app.services.modules`` is the
delivery path; this pack wraps it as a reloadable unit so the
scheduler and admin UI can hot-reload the integration without a full
application restart.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="ntfy",
    version="1.0.0",
)


__all__ = ["PACK"]
