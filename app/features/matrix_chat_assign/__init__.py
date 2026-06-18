"""Matrix Chat Auto-Assign feature pack.

Owns the admin management page for configuring chat auto-assign rules:

* ``GET  /chat/configuration``
* ``POST /chat/configuration/synonyms``
* ``POST /chat/configuration/synonyms/{group_id}``
* ``POST /chat/configuration/synonyms/{group_id}/delete``
* ``GET  /chat/auto-assign``
* ``POST /chat/auto-assign/rules``
* ``POST /chat/auto-assign/rules/{rule_id}``
* ``POST /chat/auto-assign/rules/{rule_id}/delete``
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
