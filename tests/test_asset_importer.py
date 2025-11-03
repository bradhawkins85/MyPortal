import pytest

from app.services import asset_importer, tacticalrmm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_import_tactical_assets_for_company_upserts(monkeypatch):
    company_record = {"id": 7, "tacticalrmm_client_id": "abc"}
    agents = [
        {"id": 101},
        {"id": 102},
        {"id": 101},  # duplicate agent to ensure dedupe
    ]

    async def fake_get_company(company_id):
        assert company_id == 7
        return company_record

    async def fake_fetch_agents(client_id):
        assert client_id == "abc"
        return agents

    detail_map = {
        101: {
            "name": "Workstation-1",
            "type": "server",
            "serial_number": "XYZ",
            "status": "online",
            "os_name": "Windows",
            "cpu_name": "Xeon",
            "ram_gb": 16.0,
            "hdd_size": "512 GB",
            "last_sync": "2024-01-01T10:00:00Z",
            "motherboard_manufacturer": "Dell",
            "form_factor": "Tower",
            "last_user": "Alice",
            "approx_age": 2,
            "performance_score": 92,
            "warranty_status": "active",
            "warranty_end_date": "2025-01-01",
            "tactical_asset_id": "agent-101",
        },
        102: {
            "name": "Laptop-2",
            "type": "laptop",
            "serial_number": "ABC",
            "status": "offline",
            "os_name": "Ubuntu",
            "cpu_name": "Ryzen",
            "ram_gb": 32.0,
            "hdd_size": "1 TB",
            "last_sync": "2024-01-02T12:00:00Z",
            "motherboard_manufacturer": "Lenovo",
            "form_factor": "Notebook",
            "last_user": "Bob",
            "approx_age": 1,
            "performance_score": 88,
            "warranty_status": "active",
            "warranty_end_date": "2024-12-31",
            "tactical_asset_id": "agent-102",
        },
    }

    def fake_extract(agent):
        identifier = agent.get("id")
        return detail_map[identifier]

    captured: list[dict[str, object]] = []

    async def fake_upsert_asset(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(asset_importer.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(asset_importer.tacticalrmm, "fetch_agents", fake_fetch_agents)
    monkeypatch.setattr(asset_importer.tacticalrmm, "extract_agent_details", fake_extract)
    monkeypatch.setattr(asset_importer.assets_repo, "upsert_asset", fake_upsert_asset)

    processed = await asset_importer.import_tactical_assets_for_company(7)

    assert processed == 2
    assert len(captured) == 2
    ids = {entry["tactical_asset_id"] for entry in captured}
    assert ids == {"agent-101", "agent-102"}
    assert all(entry.get("match_name") is True for entry in captured)


@pytest.mark.anyio
async def test_import_all_tactical_assets_summarises(monkeypatch):
    companies = [
        {"id": 1, "tacticalrmm_client_id": "c1"},
        {"id": 2, "tacticalrmm_client_id": "c2"},
        {"id": 3, "name": "No mapping"},
    ]

    async def fake_list_companies():
        return companies

    async def fake_import(company_id, *, tactical_client_id=None):
        if company_id == 2:
            raise tacticalrmm.TacticalRMMAPIError("boom")
        return 5

    monkeypatch.setattr(asset_importer.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(asset_importer, "import_tactical_assets_for_company", fake_import)

    summary = await asset_importer.import_all_tactical_assets()

    assert summary["processed"] == 5
    assert summary["companies"] == {1: {"processed": 5}}
    assert {item["company_id"] for item in summary["skipped"]} == {2, 3}
