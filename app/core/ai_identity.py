"""Shared identity for the support bot and troubleshooter, including old history."""
from __future__ import annotations

import html
import json
import re
from typing import Any, Mapping

from app.core.config import get_settings

TROUBLESHOOT_AGENT_EMAIL = "troubleshooting-agent@myportal.local"
_LEGACY_NAMES = re.compile(
    r"\b(?:the\s+)?(?:MyPortal Agent|AI Agent|AI Troubleshooting Agent|MyPortal troubleshooting agent|Troubleshooting Agent|"
    r"AI Waiting Assistant|AI troubleshooter|Troubleshooter agent|the troubleshooter)\b",
    re.IGNORECASE,
)


def get_agent_name() -> str:
    return str(getattr(get_settings(), "ai_agent_name", "Sophia") or "").strip() or "Sophia"


def agent_instructions() -> str:
    return f"Your name is {json.dumps(get_agent_name())}. You are the AI support assistant. "


def personalise_agent_text(value: str, *, rich_text: bool = False) -> str:
    name = get_agent_name()
    if rich_text:
        name = html.escape(name)
    return _LEGACY_NAMES.sub(lambda match: name, value)


def normalise_agent_reply(row: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    if record.get("author_id") is None and str(record.get("author_email") or "").strip().lower() == TROUBLESHOOT_AGENT_EMAIL:
        record["author_display_name"] = get_agent_name()
        if record.get("body"):
            record["body"] = personalise_agent_text(str(record["body"]), rich_text=True)
    return record


def normalise_agent_message(row: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    sender = record.get("sender_matrix_id")
    bot_id = getattr(get_settings(), "matrix_bot_user_id", None)
    if record.get("sender_user_id") is None and sender and sender in {bot_id, "@myportal-ai-waiting-assistant"}:
        record["sender_display_name"] = get_agent_name()
        if record.get("body"):
            record["body"] = personalise_agent_text(str(record["body"]))
    return record
