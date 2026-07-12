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
            "machine_type": "Physical",
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
            "machine_type": "Virtual",
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
    machine_types = {entry["machine_type"] for entry in captured}
    assert machine_types == {"Physical", "Virtual"}
    assert all(entry.get("match_name") is True for entry in captured)


@pytest.mark.anyio
async def test_import_tactical_assets_truncates_long_hdd_size(monkeypatch):
    company_record = {"id": 9, "tacticalrmm_client_id": "client-9"}
    long_hdd = "DiskModel1234567890 " * 20
    agents = [{"id": 201}]

    async def fake_get_company(company_id):
        assert company_id == 9
        return company_record

    async def fake_fetch_agents(client_id):
        assert client_id == "client-9"
        return agents

    def fake_extract(agent):
        assert agent["id"] == 201
        return {
            "name": "Storage-Heavy-Host",
            "hdd_size": long_hdd,
            "tactical_asset_id": "agent-201",
        }

    captured: list[dict[str, object]] = []

    async def fake_upsert_asset(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(asset_importer.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(asset_importer.tacticalrmm, "fetch_agents", fake_fetch_agents)
    monkeypatch.setattr(asset_importer.tacticalrmm, "extract_agent_details", fake_extract)
    monkeypatch.setattr(asset_importer.assets_repo, "upsert_asset", fake_upsert_asset)

    processed = await asset_importer.import_tactical_assets_for_company(9)

    assert processed == 1
    assert len(captured) == 1
    assert captured[0]["hdd_size"] == long_hdd[:255]
    assert len(captured[0]["hdd_size"]) == 255


@pytest.mark.anyio
async def test_import_tactical_assets_links_tray_device_from_trmm_custom_field(monkeypatch):
    company_record = {"id": 11, "tacticalrmm_client_id": "client-11"}
    agents = [
        {
            "id": 301,
            "custom_fields": [
                {"name": "TrayAgentID", "type": "text", "value": "tray-device-uid"}
            ],
        }
    ]
    linked: list[tuple[int, int]] = []

    async def fake_get_company(company_id):
        assert company_id == 11
        return company_record

    async def fake_fetch_agents(client_id):
        assert client_id == "client-11"
        return agents

    def fake_extract(agent):
        return {
            "name": "BJP-PW0F631C",
            "serial_number": "SERIAL-301",
            "tactical_asset_id": "agent-301",
        }

    async def fake_upsert_asset(**kwargs):
        return 42

    async def fake_get_device_by_uid(device_uid):
        assert device_uid == "tray-device-uid"
        return {"id": 77, "company_id": 11, "device_uid": device_uid}

    async def fake_link_device_to_asset(device_id, asset_id):
        linked.append((device_id, asset_id))

    async def fake_sync_fields(asset_id, trmm_agent_id, agent_data):
        return None

    monkeypatch.setattr(asset_importer.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(asset_importer.tacticalrmm, "fetch_agents", fake_fetch_agents)
    monkeypatch.setattr(asset_importer.tacticalrmm, "extract_agent_details", fake_extract)
    monkeypatch.setattr(asset_importer.assets_repo, "upsert_asset", fake_upsert_asset)
    monkeypatch.setattr(asset_importer.tray_repo, "get_device_by_uid", fake_get_device_by_uid)
    monkeypatch.setattr(asset_importer.tray_repo, "link_device_to_asset", fake_link_device_to_asset)
    monkeypatch.setattr(asset_importer, "_sync_tactical_asset_custom_fields", fake_sync_fields)

    processed = await asset_importer.import_tactical_assets_for_company(11)

    assert processed == 1
    assert linked == [(77, 42)]


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


@pytest.mark.anyio
async def test_import_all_tactical_assets_auto_matches_missing_mapping(monkeypatch):
    companies = [
        {"id": 3, "name": "Acme Co", "tacticalrmm_client_id": None},
    ]
    updates: list[tuple[int, str]] = []

    async def fake_list_companies():
        return companies

    async def fake_lookup(company_name: str):
        assert company_name == "Acme Co"
        return "client-300"

    async def fake_update_company(company_id: int, **kwargs):
        updates.append((company_id, kwargs["tacticalrmm_client_id"]))
        return {"id": company_id, **kwargs}

    async def fake_import(company_id, *, tactical_client_id=None):
        assert company_id == 3
        assert tactical_client_id == "client-300"
        return 2

    monkeypatch.setattr(asset_importer.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(
        asset_importer.company_id_lookup,
        "_lookup_tactical_client_id",
        fake_lookup,
    )
    monkeypatch.setattr(asset_importer.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(asset_importer, "import_tactical_assets_for_company", fake_import)

    summary = await asset_importer.import_all_tactical_assets()

    assert updates == [(3, "client-300")]
    assert summary["processed"] == 2
    assert summary["companies"] == {3: {"processed": 2}}
    assert summary["skipped"] == []
