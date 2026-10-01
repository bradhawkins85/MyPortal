"""Network map feature pack.

Draws a company's network from racks, IPAM and assets, exports it as SVG,
PNG or PDF, and documents asset interfaces and the links between them:

* ``GET /network-map`` and ``/network-map/export.svg`` / ``export.pdf``
* ``POST /api/network-map/interfaces`` and ``/api/network-map/links``

Disable it with ``DISABLED_FEATURE_PACKS=network_map``: the menu entry, the
asset page's interfaces card and every route above go away. Documented
interfaces and links stay in the database and reappear when it is enabled.
The asset type catalogue belongs to the assets pack and is unaffected.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router

PACK = FeaturePack(slug="network_map", version="1.0.0", routers=(router,))

__all__ = ["PACK"]
