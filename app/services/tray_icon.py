"""Service for the MyPortal Tray App icon.

The desktop tray UI fetches its system-tray icon from the portal so that
admins can brand it. By default the icon mirrors the website favicon
(``app/static/favicon.svg``); when an admin uploads a custom ``.ico`` file
through ``/admin/tray/branding`` we serve those bytes instead.

A Windows ``.ico`` container is produced at runtime from a procedurally
rendered PNG using only the Python standard library, so no binary asset
files need to be committed to the repository.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Optional

from app.repositories import site_settings as site_settings_repo

# Palette pulled from app/static/favicon.svg (slate background, sky→indigo
# gradient circle, slate inner dot). The procedural raster only needs
# representative solid colours – no gradient – which is good enough at the
# 32×32 size the system tray actually displays.
_BG_COLOR = (0x0F, 0x17, 0x2A, 0xFF)      # #0f172a
_RING_COLOR = (0x4D, 0x90, 0xD4, 0xFF)    # midpoint of #38bdf8 and #6366f1
_INNER_COLOR = (0x0F, 0x17, 0x2A, 0xFF)   # #0f172a

_ICON_SIZE = 32
_ICON_RADIUS = 8         # rounded square corner radius
_RING_RADIUS = 11
_INNER_RADIUS = 6

_ICO_MAGIC = b"\x00\x00\x01\x00"

_default_icon_cache: Optional[bytes] = None


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _build_default_png() -> bytes:
    """Build a 32×32 RGBA PNG matching the website favicon palette."""
    size = _ICON_SIZE
    cx = cy = (size - 1) / 2.0
    rounded_inset = _ICON_RADIUS

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter byte: None
        for x in range(size):
            # Rounded square mask: clip the four corner regions.
            if (
                (x < rounded_inset and y < rounded_inset
                 and (rounded_inset - x) ** 2 + (rounded_inset - y) ** 2 > rounded_inset ** 2)
                or (x >= size - rounded_inset and y < rounded_inset
                    and (x - (size - 1 - rounded_inset)) ** 2
                    + (rounded_inset - y) ** 2 > rounded_inset ** 2)
                or (x < rounded_inset and y >= size - rounded_inset
                    and (rounded_inset - x) ** 2
                    + (y - (size - 1 - rounded_inset)) ** 2 > rounded_inset ** 2)
                or (x >= size - rounded_inset and y >= size - rounded_inset
                    and (x - (size - 1 - rounded_inset)) ** 2
                    + (y - (size - 1 - rounded_inset)) ** 2 > rounded_inset ** 2)
            ):
                rows.extend((0, 0, 0, 0))
                continue

            dx = x - cx
            dy = y - cy
            dist_sq = dx * dx + dy * dy
            if dist_sq <= _INNER_RADIUS * _INNER_RADIUS:
                rows.extend(_INNER_COLOR)
            elif dist_sq <= _RING_RADIUS * _RING_RADIUS:
                rows.extend(_RING_COLOR)
            else:
                rows.extend(_BG_COLOR)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(rows), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _wrap_png_in_ico(png_data: bytes) -> bytes:
    """Wrap a PNG in a single-image Windows ICO container."""
    png_len = len(png_data)
    # 6-byte ICONDIR + 16-byte ICONDIRENTRY + payload.
    header = (
        _ICO_MAGIC
        + struct.pack("<H", 1)  # number of images
    )
    entry = struct.pack(
        "<BBBBHHII",
        _ICON_SIZE if _ICON_SIZE < 256 else 0,  # width (0 == 256)
        _ICON_SIZE if _ICON_SIZE < 256 else 0,  # height
        0,   # palette colour count (0 = no palette / true colour)
        0,   # reserved
        1,   # colour planes
        32,  # bits per pixel
        png_len,
        len(header) + 16,  # offset to image data
    )
    return header + entry + png_data


def build_default_icon_bytes() -> bytes:
    """Return a default Windows ``.ico`` derived from the website favicon."""
    global _default_icon_cache
    if _default_icon_cache is None:
        _default_icon_cache = _wrap_png_in_ico(_build_default_png())
    return _default_icon_cache


def is_valid_ico(data: bytes) -> bool:
    """Validate the ICO magic bytes (00 00 01 00) and minimum length."""
    return len(data) >= 22 and data[:4] == _ICO_MAGIC


async def get_tray_icon_bytes(uploads_root: Path) -> bytes:
    """Return the active tray icon bytes (uploaded override or default).

    Always returns a usable icon; any database or filesystem error falls back
    to the procedurally generated default so the public ``/tray/icon.ico``
    endpoint never fails.
    """
    try:
        relative_path = await site_settings_repo.get_tray_icon_path()
    except Exception:  # pragma: no cover - defensive (e.g. table missing)
        relative_path = None
    if relative_path:
        try:
            candidate = (uploads_root / relative_path).resolve()
            uploads_root_resolved = uploads_root.resolve()
            # Ensure the resolved path stays within uploads_root to avoid
            # path-traversal via a poisoned database value.
            if (
                uploads_root_resolved == candidate
                or uploads_root_resolved in candidate.parents
            ) and candidate.is_file():
                data = candidate.read_bytes()
                if is_valid_ico(data):
                    return data
        except OSError:
            pass
    return build_default_icon_bytes()


__all__ = [
    "build_default_icon_bytes",
    "get_tray_icon_bytes",
    "is_valid_ico",
]
