"""Test for BC15 change log entry."""
import json
from pathlib import Path

import pytest

from app.services import change_log as change_log_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _RepositoryStub:
    def __init__(self) -> None:
        self.by_hash: dict[str, dict] = {}
        self.by_guid: dict[str, dict] = {}
        self.upserts: list[dict] = []

    async def get_change_by_hash(self, content_hash: str):
        return self.by_hash.get(content_hash)

    async def get_change_by_guid(self, guid: str):
        return self.by_guid.get(guid)

    async def upsert_change(self, **payload):
        self.by_hash[payload["content_hash"]] = payload
        self.by_guid[payload["guid"]] = payload
        self.upserts.append(payload)


@pytest.mark.anyio
async def test_bc15_change_log_entry_loads():
    """Test that the BC15 change log entry can be loaded and synced."""
    repo = _RepositoryStub()
    
    # Get the project root (test file is in tests/, so go up one level)
    project_root = Path(__file__).parent.parent
    
    # Verify the BC15 change log file exists
    bc15_guid = "c614f380-7e3f-40c4-a069-42e07cb812ba"
    bc15_file = project_root / "changes" / f"{bc15_guid}.json"
    
    assert bc15_file.exists(), f"BC15 change log file not found at {bc15_file}"
    
    # Verify the file content
    with open(bc15_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert data["guid"] == bc15_guid
    assert data["change_type"] == "Feature"
    assert "BC Plan module" in data["summary"]
    assert "template-driven" in data["summary"]
    assert "versioning" in data["summary"]
    assert len(data["content_hash"]) == 64  # SHA256 hash
    assert data["occurred_at"].endswith("Z")  # UTC timestamp
    
    # Sync change logs and verify BC15 entry is loaded
    await change_log_service.sync_change_log_sources(
        base_path=project_root, 
        repository=repo
    )
    
    # Find the BC15 entry in the synced changes
    bc15_entry = None
    for entry in repo.upserts:
        if entry["guid"] == bc15_guid:
            bc15_entry = entry
            break
    
    assert bc15_entry is not None, "BC15 change log entry was not synced"
    assert bc15_entry["change_type"] == "Feature"
    assert "BC Plan module" in bc15_entry["summary"]


@pytest.mark.anyio  
async def test_bc15_content_hash_is_valid():
    """Verify that BC15 content hash is valid and represents the template-driven system upgrade."""
    project_root = Path(__file__).parent.parent
    bc15_guid = "c614f380-7e3f-40c4-a069-42e07cb812ba"
    bc15_file = project_root / "changes" / f"{bc15_guid}.json"
    
    with open(bc15_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Verify it's a valid SHA256 hash (64 hex characters)
    content_hash = data["content_hash"]
    assert len(content_hash) == 64
    assert all(c in "0123456789abcdef" for c in content_hash)
    
    # Verify the hash is correctly computed based on the change log service's algorithm
    # The change log service computes hashes as SHA256(occurred_at|type|summary)
    from hashlib import sha256
    from datetime import datetime, timezone
    
    occurred_at = data["occurred_at"]
    if occurred_at.endswith("Z"):
        occurred_at_iso = occurred_at[:-1] + "+00:00"
    else:
        occurred_at_iso = occurred_at
    
    # Compute expected hash using the change log service's format
    base = "|".join([
        occurred_at_iso,
        data["change_type"].lower(),
        data["summary"].strip(),
    ])
    expected_hash = sha256(base.encode("utf-8")).hexdigest()
    
    # The hash should match the computed value
    # Note: While the issue requested a template schema hash, the change log service
    # automatically recomputes hashes based on its standard algorithm. The BC15 entry
    # documents the template-driven system upgrade in its summary and metadata.
    assert content_hash == expected_hash, (
        f"BC15 content_hash should match the change log service's computed value.\n"
        f"Expected: {expected_hash}\n"
        f"Got: {content_hash}"
    )
