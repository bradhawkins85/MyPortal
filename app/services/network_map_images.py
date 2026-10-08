"""Identification images for the visible devices in a Detailed map PDF.

Only attached rack images and authorised asset photos are considered. Embed
validated local files so PDF rendering needs no authenticated image requests.
"""
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.repositories import asset_photos as asset_photo_repo
from app.repositories import rack_item_images as rack_image_repo
from app.services import asset_photos, network_map, rack_item_images

_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


def _image_uri(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            payload = stream.read(asset_photos.MAX_BYTES + 1)
        if len(payload) > asset_photos.MAX_BYTES:
            return None
        with Image.open(BytesIO(payload)) as image:
            content_type = _MIME_TYPES.get(image.format or "")
            image.verify()
        if not content_type:
            return None
        return "data:" + content_type + ";base64," + base64.b64encode(payload).decode("ascii")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


async def identification_images(
    company_id: int, graph: network_map.Graph, *, allow_rack_images: bool = True,
    asset_photo_access: Callable[[int], Awaitable[bool | None]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer product images, then device images, then accessible asset photos.

    The photo access callback returns customer_only, or None when access is
    denied. Keep each map device once even when it has multiple rack placements.
    Missing or invalid files do not prevent falling back to the next source.
    """
    if graph.options.detail != "detailed":
        return []
    candidates: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if allow_rack_images:
        for image in await rack_image_repo.equipment_image_files(company_id):
            asset_node_id = "asset:" + str(image.get("asset_id"))
            node_id = asset_node_id if asset_node_id in graph.nodes else "item:" + str(image["equipment_id"])
            if node_id not in graph.nodes:
                continue
            grouped = candidates.setdefault(node_id, {"product": [], "device": []})
            grouped[image["kind"]].append(image)

    cache: dict[Path, str | None] = {}

    def embedded_image(path: Path, caption: Any) -> dict[str, str] | None:
        if path not in cache:
            cache[path] = _image_uri(path)
        uri = cache[path]
        return {"src": uri, "caption": str(caption or "")} if uri else None

    entries = []
    nodes = sorted(graph.nodes.values(), key=lambda node: (node.site, node.rack_name or "~", -node.start_unit, node.label.casefold()))
    for node in nodes:
        if node.kind not in {"asset", "item"}:
            continue
        images: list[dict[str, str]] = []
        source = ""
        for kind in ("product", "device"):
            seen_paths: set[Path] = set()
            for candidate in candidates.get(node.id, {}).get(kind, []):
                try:
                    path = rack_item_images.resolve_rack_image(str(candidate["storage_name"]))
                except HTTPException:
                    continue
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                image = embedded_image(path, candidate.get("caption"))
                if image:
                    images.append(image)
            if images:
                source = "Product image" if kind == "product" else "Device image"
                break
        if not images and node.kind == "asset" and asset_photo_access:
            asset_id = int(node.id.partition(":")[2])
            customer_only = await asset_photo_access(asset_id)
            if customer_only is not None:
                for photo in await asset_photo_repo.list_for_asset(company_id, asset_id, customer_only=customer_only):
                    try:
                        path = asset_photos.path(company_id, asset_id, str(photo["storage_name"]))
                    except HTTPException:
                        continue
                    image = embedded_image(path, photo.get("caption"))
                    if image:
                        images.append(image)
                if images:
                    source = "Asset photo"
        if images:
            entries.append({"name": node.label, "type": node.type_label, "site": node.site,
                            "rack": node.rack_name, "ips": node.ips, "facts": node.facts,
                            "images": images, "source": source})
    return entries
