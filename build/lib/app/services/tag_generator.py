from __future__ import annotations

from typing import Any, Mapping

from app.core.logging import log_error
from app.services import modules as modules_service


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
    
    # Build a concise prompt for tag generation
    context_parts = [f"Service name: {name.strip()}"]
    if description and description.strip():
        context_parts.append(f"Description: {description.strip()}")
    
    context = "\n".join(context_parts)
    
    prompt = f"""Generate 3-5 relevant tags for this service. Tags should be single words or short phrases (2-3 words max) that categorize the service.

{context}

Return ONLY a comma-separated list of tags, nothing else. Example format: infrastructure, monitoring, cloud, availability
Tags:"""
    
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
    status = response.get("status")
    if status not in ("completed", "success"):
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
    
    # Parse the comma-separated tags
    tags = []
    for tag in tags_text.strip().split(","):
        cleaned = tag.strip().lower()
        # Remove any extra punctuation or formatting
        cleaned = cleaned.strip(".:;!?'\"")
        if cleaned and len(cleaned) <= 50:  # Reasonable tag length limit
            tags.append(cleaned)
    
    return tags[:5]  # Limit to 5 tags max
