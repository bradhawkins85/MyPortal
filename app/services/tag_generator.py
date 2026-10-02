from __future__ import annotations

import re
from typing import Mapping

from app.core.logging import log_error
from app.services import modules as modules_service
from app.services.ai_prompt_security import UntrustedRecord, build_prompt

_TAG_VALUE_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]*$")


async def generate_tags_for_service(
    name: str,
    description: str | None = None,
) -> list[str]:
    """
    Generate relevant tags for a service using Ollama AI.
    
    Args:
        name: The service name
        description: Optional service description
        
    Returns:
        List of tags (empty list if generation fails)
    """
    if not name or not name.strip():
        return []

    records = [
        UntrustedRecord(
            "service",
            "service name and description",
            {"name": name.strip(), "description": (description or "").strip()},
            "Use only to derive categorizing tags",
        )
    ]
    prompt = build_prompt(
        "Generate 3-5 relevant tags for this service. Tags should be single words or "
        "short phrases (2-3 words max) that categorize the service. "
        "Return ONLY a comma-separated list of tags, nothing else. "
        "Example format: infrastructure, monitoring, cloud, availability",
        records,
    )
    
    try:
        response = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt},
            background=False,
        )
    except ValueError as exc:
        # Module not configured or disabled
        log_error("Tag generation failed - Ollama module not available", error=str(exc))
        return []
    except Exception as exc:
        log_error("Tag generation failed - unexpected error", error=str(exc))
        return []
    
    # Check if the module was successful
    if not modules_service.module_result_succeeded(response):
        return []
    
    # Extract the response text
    response_data = response.get("response")
    if isinstance(response_data, Mapping):
        tags_text = response_data.get("response") or response_data.get("message")
    elif isinstance(response_data, str):
        tags_text = response_data
    else:
        tags_text = response.get("message")
    
    if not tags_text or not isinstance(tags_text, str):
        return []
    
    # Parse the comma-separated tags, discarding anything that is not a clean
    # short value so injected/malformed output is never stored.
    tags: list[str] = []
    for tag in tags_text.strip().split(","):
        cleaned = tag.strip().lower()
        # Remove any extra punctuation or formatting
        cleaned = cleaned.strip(".:;!?'\"")
        if cleaned and len(cleaned) <= 50:  # Reasonable tag length limit
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not _TAG_VALUE_RE.match(cleaned):
                continue
            tags.append(cleaned)
    
    return tags[:5]  # Limit to 5 tags max
