"""Matrix Chat Auto-Assign feature pack.

Owns the admin management page for configuring chat auto-assign rules:

* ``GET  /admin/modules/matrix-chat-assign``
* ``POST /admin/modules/matrix-chat-assign/rules``
* ``POST /admin/modules/matrix-chat-assign/rules/{rule_id}``
* ``POST /admin/modules/matrix-chat-assign/rules/{rule_id}/delete``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router


PACK = FeaturePack(
    slug="matrix_chat_assign",
    version="1.0.0",
    routers=(router,),
)

__all__ = ["PACK"]
