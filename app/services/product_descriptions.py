from __future__ import annotations

import html
import json
import re
from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.repositories import shop as shop_repo
from app.services import modules as modules_service
from app.services.sanitization import sanitize_rich_text

_PROMPT = """Below is a product description, without modifying the specifications redesign the layout in a user-friendly and easily readable way.

Return JSON only with this shape:
{"description_html":"safe HTML using headings, lists and tables", "features":[{"name":"Feature name","value":"Feature value"}]}

Product description:
{description}
"""

_KEY_VALUE_RE = re.compile(r"^\s*([^:\-–—|]{2,80})\s*[:\-–—|]\s*(.{1,300})\s*$")
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(value: str | None) -> str:
    sanitized = sanitize_rich_text(value or "")
    text = re.sub(r"<br\s*/?>", "\n", sanitized.html, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|td|th|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def _normalise_feature_name(name: str) -> str:
    name = re.sub(r"[_\s]+", " ", name).strip(" -:|\t\r\n")
    return name[:255]


def extract_features(description: str | None, *, limit: int = 30) -> list[dict[str, Any]]:
    text = _plain_text(description)
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+| {2,}|;\s*", text):
        line = raw_line.strip(" •-*\t")
        if not line or len(line) > 360:
            continue
        match = _KEY_VALUE_RE.match(line)
        if not match:
            continue
        name = _normalise_feature_name(match.group(1))
        value = match.group(2).strip()
        if not name or not value:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        features.append({"name": name, "value": value, "position": len(features)})
        if len(features) >= limit:
            break
    return features


def _fallback_layout(description: str | None) -> str | None:
    text = _plain_text(description)
    if not text:
        return None
    features = extract_features(text)
    remaining = []
    feature_keys = {f"{f['name'].lower()}:{str(f.get('value') or '').lower()}" for f in features}
    for line in [part.strip(" •-*\t") for part in re.split(r"[\r\n]+", text)]:
        match = _KEY_VALUE_RE.match(line)
        if match:
            key = f"{_normalise_feature_name(match.group(1)).lower()}:{match.group(2).strip().lower()}"
            if key in feature_keys:
                continue
        if line:
            remaining.append(line)
    parts: list[str] = []
    if remaining:
        parts.append("<h3>Overview</h3>")
        parts.extend(f"<p>{html.escape(line)}</p>" for line in remaining[:8])
    if features:
        parts.append("<h3>Specifications</h3><dl>")
        for feature in features:
            parts.append(f"<dt>{html.escape(str(feature['name']))}</dt><dd>{html.escape(str(feature.get('value') or ''))}</dd>")
        parts.append("</dl>")
    return sanitize_rich_text("\n".join(parts)).html or None


def _parse_ai_payload(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    if isinstance(payload, Mapping):
        text = payload.get("response") or payload.get("text") or payload.get("content") or payload
    else:
        text = payload
    if isinstance(text, Mapping):
        data = dict(text)
    else:
        raw = str(text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return sanitize_rich_text(raw).html if raw else None, []
    desc = data.get("description_html") or data.get("description") or data.get("html")
    raw_features = data.get("features") or []
    features: list[dict[str, Any]] = []
    if isinstance(raw_features, list):
        for item in raw_features:
            if not isinstance(item, Mapping):
                continue
            name = _normalise_feature_name(str(item.get("name") or item.get("feature") or ""))
            value = str(item.get("value") or item.get("specification") or "").strip()
            if name and value:
                features.append({"name": name, "value": value, "position": len(features)})
    return sanitize_rich_text(str(desc)).html if desc else None, features


async def improve_product_description(product_id: int) -> dict[str, Any] | None:
    product = await shop_repo.get_product_by_id(product_id, include_archived=True)
    if not product:
        return None
    original = str(product.get("description") or "").strip()
    if not original:
        await shop_repo.replace_product_features(product_id, [])
        return {"description": None, "features": []}

    description_html: str | None = None
    features: list[dict[str, Any]] = []
    try:
        response = await modules_service.trigger_module("ollama", {"prompt": _PROMPT.format(description=original), "format": "json"})
        status = str(response.get("status") or "") if isinstance(response, Mapping) else ""
        if status not in {"skipped", "error"}:
            description_html, features = _parse_ai_payload(response.get("response") if isinstance(response, Mapping) else response)
    except ValueError:
        pass
    except Exception as exc:  # pragma: no cover - external AI/network failures
        log_error("Product description AI refresh failed; using local formatting", product_id=product_id, error=str(exc))

    if not description_html:
        description_html = _fallback_layout(original)
    if not features:
        features = extract_features(original)

    updated = await shop_repo.update_product_description(product_id, description_html)
    await shop_repo.replace_product_features(product_id, features)
    log_info("Product description refreshed", product_id=product_id, feature_count=len(features), ai_used=bool(description_html))
    return {"description": updated.get("description") if updated else description_html, "features": features}
