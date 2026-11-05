"""Attachment handling for chat messages (photos, videos, audio, files, locations).

Matrix media messages carry an ``mxc://`` content URI plus details such as the
file name, type and size.  Those details are stored as JSON in
``chat_messages.media_json`` and presented to the tray chat window through a
proxy endpoint, so end users never need direct access to the homeserver.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.services.matrix import parse_mxc_uri

MEDIA_MSGTYPES = frozenset({"m.image", "m.video", "m.audio", "m.file"})
LOCATION_MSGTYPE = "m.location"

# Largest attachment an end user can send from the tray chat window, matching
# Synapse's default max_upload_size.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Largest attachment the portal will relay to the tray chat window.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Types the chat window may show inline.  Anything else is always served as a
# download so the browser never renders untrusted active content (HTML, SVG…).
INLINE_MIMETYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "video/mp4",
        "video/webm",
        "video/ogg",
        "video/quicktime",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/ogg",
        "audio/webm",
        "audio/wav",
        "audio/x-wav",
    }
)

_GEO_URI_RE = re.compile(r"^geo:(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)(?:[;,].*)?$")
_MIMETYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


def clean_mimetype(value: Any) -> str:
    mimetype = str(value or "").split(";", 1)[0].strip().lower()
    return mimetype if _MIMETYPE_RE.fullmatch(mimetype) else ""


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


def clean_filename(value: Any, *, fallback: str = "attachment") -> str:
    """Return a display-safe file name without paths or control characters."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    return name[:255] or fallback


def parse_geo_uri(geo_uri: Any) -> tuple[float, float] | None:
    match = _GEO_URI_RE.fullmatch(str(geo_uri or "").strip())
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def media_from_content(msgtype: str, content: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the attachment details worth storing from a Matrix message content."""
    if msgtype in MEDIA_MSGTYPES:
        url = str(content.get("url") or "")
        if not parse_mxc_uri(url):
            # Encrypted attachments (content.file) cannot be relayed by the bot.
            return None
        info = content.get("info") if isinstance(content.get("info"), Mapping) else {}
        media: dict[str, Any] = {
            "url": url,
            "filename": clean_filename(content.get("filename") or content.get("body")),
            "mimetype": clean_mimetype(info.get("mimetype")),
        }
        for key, source in (("size", "size"), ("width", "w"), ("height", "h"), ("duration", "duration")):
            number = _clean_int(info.get(source))
            if number is not None:
                media[key] = number
        return media
    if msgtype == LOCATION_MSGTYPE:
        geo_uri = str(content.get("geo_uri") or "").strip()
        if not parse_geo_uri(geo_uri):
            return None
        return {"geo_uri": geo_uri}
    return None


def msgtype_for_mimetype(mimetype: str) -> str:
    major = mimetype.split("/", 1)[0]
    return {"image": "m.image", "video": "m.video", "audio": "m.audio"}.get(major, "m.file")


def dump_media(media: Mapping[str, Any] | None) -> str | None:
    return json.dumps(dict(media), separators=(",", ":")) if media else None


def load_media(message: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = message.get("media_json")
    if not raw:
        return None
    try:
        media = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return None
    return media if isinstance(media, dict) else None


def inline_mimetype(mimetype: str) -> bool:
    return clean_mimetype(mimetype) in INLINE_MIMETYPES


def public_media(message: Mapping[str, Any], *, media_url: str) -> dict[str, Any] | None:
    """Describe a message's attachment for the chat window, without the mxc URI."""
    media = load_media(message)
    if not media:
        return None
    msgtype = str(message.get("msgtype") or "")
    if msgtype == LOCATION_MSGTYPE:
        coords = parse_geo_uri(media.get("geo_uri"))
        if not coords:
            return None
        return {"kind": "location", "latitude": coords[0], "longitude": coords[1]}
    if msgtype not in MEDIA_MSGTYPES or not media.get("url"):
        return None
    mimetype = clean_mimetype(media.get("mimetype"))
    kind = {"m.image": "image", "m.video": "video", "m.audio": "audio"}.get(msgtype, "file")
    if kind != "file" and not inline_mimetype(mimetype):
        kind = "file"
    public: dict[str, Any] = {
        "kind": kind,
        "filename": clean_filename(media.get("filename")),
        "mimetype": mimetype,
        "url": media_url,
        "download_url": media_url + "?download=1",
    }
    for key in ("size", "width", "height", "duration"):
        if isinstance(media.get(key), int):
            public[key] = media[key]
    return public


def popup_message_view(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return a tray chat window message: no raw media JSON, plus a ``media`` summary."""
    view = {key: value for key, value in dict(message).items() if key != "media_json"}
    media = None
    try:
        room_id = int(message.get("room_id") or 0)
        message_id = int(message.get("id") or 0)
    except (TypeError, ValueError):
        room_id = message_id = 0
    if room_id > 0 and message_id > 0:
        media_url = "/api/tray/popup-chat/" + str(room_id) + "/messages/" + str(message_id) + "/media"
        media = public_media(message, media_url=media_url)
    view["media"] = media
    return view
