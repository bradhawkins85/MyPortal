"""Fingerprint identifying which embedding configuration produced a vector.

Kept free of repository imports so both the indexing service and the derived
vector index can depend on it without an import cycle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.config import get_settings

_EMBEDDING_ALGORITHM = "myportal-embedding-v3"


def embedding_model(settings: Any | None = None) -> str:
    """Return the persisted compatibility fingerprint for the active vectors."""
    settings = settings or get_settings()
    components = {
        "algorithm": _EMBEDDING_ALGORITHM,
        "provider": settings.rag_embedding_provider.strip().lower(),
        "model": settings.rag_embedding_model.strip(),
        "dimensions": int(settings.rag_embedding_dimensions),
    }
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"{components['provider']}:{components['model']}:{components['dimensions']}:{digest}"

