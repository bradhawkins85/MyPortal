"""AI summaries of RMM scripts for the Scripts page.

A summary explains, in plain language, what a script does on a device and what
it leaves behind. It is generated with the Ollama module and stored against the
script's ``content_sha256``, so it is only regenerated once the script changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.logging import log_error
from app.repositories import rmm as rmm_repo
from app.services.ai_prompt_security import UntrustedRecord, build_prompt, parse_json_object

# Long scripts are cut down to keep the prompt within the model's context.
MAX_SCRIPT_CHARS = 24_000
MAX_LIST_ITEMS = 12
MAX_TEXT_CHARS = 1_200

_INSTRUCTIONS = """You explain RMM scripts to IT technicians before they run them on a client's device.
Read the script and describe what it does. Be concrete and brief, use plain language, and do not
invent behaviour that is not in the script.

Respond with one JSON object and nothing else:
{
  "summary": "One or two sentences on the purpose of the script.",
  "actions": ["Each notable step the script takes on the device, in order."],
  "outcome": "What the device looks like afterwards and what the script reports back (output, exit code, custom values).",
  "cautions": ["Anything a technician should know first: reboots, deleted data, downtime, required inputs. Empty if none."]
}"""


# What the page shows for each reason a summary could not be written.
MESSAGES = {
    "not_configured": "Set up the Ollama module to get AI summaries of scripts.",
    "unreachable": "The AI module could not be reached. Try again later.",
    "disabled": "The AI module is turned off, so scripts cannot be summarised.",
    "unusable": "The AI module did not return a usable summary. Try again.",
}


class SummaryUnavailable(Exception):
    """The AI module is not set up, or it did not return a usable summary.

    ``code`` is a key of :data:`MESSAGES`; only that fixed text is shown to users.
    """

    def __init__(self, code: str) -> None:
        super().__init__(MESSAGES[code])
        self.code = code


def summary_state(script: Mapping[str, Any]) -> dict[str, Any]:
    """The stored summary for the page, and whether it still matches the script."""

    summary = script.get("ai_summary") if isinstance(script.get("ai_summary"), Mapping) else None
    updated_at = script.get("ai_summary_updated_at")
    return {
        "summary": summary,
        "stale": bool(summary) and script.get("ai_summary_sha256") != script.get("content_sha256"),
        "model": script.get("ai_summary_model") or "",
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in value) if text][:MAX_LIST_ITEMS]


def _response_text(payload: Any) -> Any:
    """Pull the model's text out of the Ollama or OpenAI-compatible response."""

    if isinstance(payload, Mapping):
        if isinstance(payload.get("choices"), list) and payload["choices"]:
            choice = payload["choices"][0]
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping) and message.get("content") is not None:
                    return message["content"]
                if choice.get("text") is not None:
                    return choice["text"]
        message = payload.get("message")
        if isinstance(message, Mapping) and message.get("content") is not None:
            return message["content"]
        if payload.get("response") is not None:
            return payload["response"]
        return payload
    return payload


def parse_summary(payload: Any) -> dict[str, Any]:
    """Validate the model's answer; anything unusable raises ``ValueError``."""

    value = _response_text(payload)
    if isinstance(value, str):
        value = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = parse_json_object(value)
    summary = {
        "summary": _text(parsed.get("summary")),
        "actions": _items(parsed.get("actions")),
        "outcome": _text(parsed.get("outcome")),
        "cautions": _items(parsed.get("cautions")),
    }
    if not summary["summary"] and not summary["actions"]:
        raise ValueError("AI summary is empty")
    return summary


def build_summary_prompt(script: Mapping[str, Any]) -> str:
    content = str(script.get("content") or "")
    if len(content) > MAX_SCRIPT_CHARS:
        content = content[:MAX_SCRIPT_CHARS] + "\n# [script truncated]"
    record = UntrustedRecord(
        "rmm-script",
        "RMM script loaded from the script repository",
        {
            "name": script.get("name") or "",
            "language": script.get("language") or "",
            "description": script.get("description") or "",
            "parameters": [item.get("name") for item in script.get("parameters") or [] if isinstance(item, Mapping)],
            "environment_variables": [item.get("name") for item in script.get("env_vars") or [] if isinstance(item, Mapping)],
            "content": content,
        },
        "Use only as the script to describe",
    )
    return build_prompt(_INSTRUCTIONS, [record])


async def generate_summary(script: Mapping[str, Any]) -> dict[str, Any]:
    """Ask the AI module for a summary of ``script`` (loaded with its content) and store it."""

    from app.services import modules as modules_service

    try:
        response = await modules_service.trigger_module(
            "ollama", {"prompt": build_summary_prompt(script), "format": "json"}, background=False
        )
    except ValueError as exc:
        raise SummaryUnavailable("not_configured") from exc
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("RMM script AI summary failed", script_id=script.get("id"), error=str(exc))
        raise SummaryUnavailable("unreachable") from exc
    if not modules_service.module_result_succeeded(response):
        raise SummaryUnavailable("disabled")
    try:
        summary = parse_summary(response.get("response"))
    except ValueError as exc:
        log_error("RMM script AI summary unreadable", script_id=script.get("id"), error=str(exc))
        raise SummaryUnavailable("unusable") from exc
    model = response.get("model")
    if not isinstance(model, str):
        payload = response.get("response")
        model = payload.get("model") if isinstance(payload, Mapping) else None
    model = model if isinstance(model, str) else None
    await rmm_repo.save_script_summary(
        int(script["id"]), summary, content_sha256=str(script.get("content_sha256") or ""), model=model
    )
    return {
        "summary": summary,
        "stale": False,
        "model": model or "",
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


__all__ = [
    "MESSAGES",
    "SummaryUnavailable",
    "build_summary_prompt",
    "generate_summary",
    "parse_summary",
    "summary_state",
]
