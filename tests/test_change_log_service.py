import json

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

    async def upsert_change(self, **payload):  # pragma: no cover - exercised indirectly
        self.by_hash[payload["content_hash"]] = payload
        self.by_guid[payload["guid"]] = payload
        self.upserts.append(payload)


@pytest.mark.anyio
async def test_sync_change_log_sources_imports_json_and_markdown(tmp_path):
    repo = _RepositoryStub()

    changes_dir = tmp_path / "changes"
    changes_dir.mkdir()
    existing_guid = "11111111-1111-4111-8111-111111111111"
    (changes_dir / f"{existing_guid}.json").write_text(
        json.dumps(
            {
                "guid": existing_guid,
                "occurred_at": "2025-10-23T01:20Z",
                "change_type": "Feature",
                "summary": "Added new scheduler control",
                "content_hash": "",
            }
        ),
        encoding="utf-8",
    )

    (tmp_path / "changes.md").write_text(
        "- 2025-10-24, 06:15 UTC, Fix, Corrected webhook retry counter\n",
        encoding="utf-8",
    )

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)

    assert len(repo.upserts) == 2
    summaries = sorted(item["summary"] for item in repo.upserts)
    assert summaries == ["Added new scheduler control", "Corrected webhook retry counter"]

    created_files = sorted(p.name for p in changes_dir.glob("*.json"))
    assert len(created_files) == 2
    assert any(name != f"{existing_guid}.json" for name in created_files)

    with open(changes_dir / created_files[0], "r", encoding="utf-8") as handle:
        data = json.load(handle)
        assert "guid" in data
        assert data["occurred_at"].endswith("Z")


@pytest.mark.anyio
async def test_sync_change_log_sources_reuses_existing_entries(tmp_path):
    repo = _RepositoryStub()

    (tmp_path / "changes.md").write_text(
        "- 2025-10-24, 06:15 UTC, Feature, Added webhook monitor\n",
        encoding="utf-8",
    )

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)
    first_guid = repo.upserts[0]["guid"]
    assert first_guid in repo.by_guid

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)
    assert len(repo.upserts) == 1
    assert repo.upserts[0]["guid"] == first_guid

    files = list((tmp_path / "changes").glob("*.json"))
    assert len(files) == 1


@pytest.mark.anyio
async def test_sync_change_log_sources_processes_modified_files(tmp_path):
    repo = _RepositoryStub()

    changes_dir = tmp_path / "changes"
    changes_dir.mkdir()
    existing_guid = "11111111-1111-4111-8111-222222222222"
    entry_path = changes_dir / f"{existing_guid}.json"
    entry_path.write_text(
        json.dumps(
            {
                "guid": existing_guid,
                "occurred_at": "2025-10-23T01:20Z",
                "change_type": "Feature",
                "summary": "Initial summary",
                "content_hash": "",
            }
        ),
        encoding="utf-8",
    )

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)
    assert len(repo.upserts) == 1

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)
    assert len(repo.upserts) == 1

    data = json.loads(entry_path.read_text(encoding="utf-8"))
    data["summary"] = "Updated summary"
    entry_path.write_text(json.dumps(data), encoding="utf-8")

    await change_log_service.sync_change_log_sources(base_path=tmp_path, repository=repo)
    assert len(repo.upserts) == 2
    assert repo.upserts[-1]["summary"] == "Updated summary"
