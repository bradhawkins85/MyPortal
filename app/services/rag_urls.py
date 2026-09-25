from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit


def _local_url(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return None
    return candidate


def canonical_source_url(
    source_type: str,
    source_id: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
    supplied_url: Any = None,
) -> str | None:
    """Return a safe portal destination for a linkable indexed source."""
    supplied = _local_url(supplied_url)
    if supplied:
        return supplied
    kind = str(source_type or "").strip().casefold()
    details = metadata or {}
    identifier = str(source_id or "").strip()
    if not identifier:
        return None
    encoded = quote(identifier, safe="")
    if kind == "knowledge_base":
        slug = str(details.get("slug") or identifier).strip()
        return f"/knowledge-base/articles/{quote(slug, safe='')}"
    routes = {
        "tickets": "/admin/tickets/{}",
        # Assets are owned by the assets feature pack.  There is deliberately no
        # parallel admin asset route (see the ITDOC architecture contract).
        "assets": "/assets/{}",
        "companies": "/admin/companies/{}",
        "staff": "/admin/staff/{}",
        "chats": "/chat/{}",
        "issues": "/admin/issues/{}",
        "products": "/shop/admin/product/{}",
    }
    if kind in routes:
        return routes[kind].format(encoded)
    if kind == "orders":
        return "/orders?" + urlencode({"search": str(details.get("order_number") or identifier).strip()})
    if kind == "reports":
        return "/reports/company-overview"
    return None
