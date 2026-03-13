import pytest

from app.services import tacticalrmm


def test_extract_agent_page_handles_beta_results():
    response = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": 20,
                "hostname": "BJP-LAB-ACQ1",
                "agent_id": "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz",
                "operating_system": "Windows 10 Pro",
            }
        ],
    }

    items, next_endpoint = tacticalrmm._extract_agent_page(response, "https://example.com")

    assert len(items) == 1
    assert items[0]["hostname"] == "BJP-LAB-ACQ1"
    assert next_endpoint is None


def test_extract_agent_details_handles_beta_agent_payload():
    agent = {
        "id": 20,
        "hostname": "BJP-LAB-ACQ1",
        "agent_id": "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz",
        "operating_system": "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)",
        "last_seen": "2025-11-04T02:40:20.840228Z",
        "plat": "windows",
        "services": [],
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["name"] == "BJP-LAB-ACQ1"
    assert details["os_name"] == "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)"
    assert details["tactical_asset_id"] == "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz"
    assert details["last_sync"] == "2025-11-04T02:40:20.840228Z"


def test_extract_agent_details_joins_cpu_model_list():
    """cpu_model from the real TacticalRMM API is a list – it should be joined."""
    agent = {
        "hostname": "WORKSTATION-01",
        "agent_id": "abc123",
        "cpu_model": ["Intel Core i7-8700, 6C/12T", "Intel Core i7-8700, 6C/12T"],
        "operating_system": "Windows 10 Pro",
        "monitoring_type": "workstation",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["cpu_name"] == "Intel Core i7-8700, 6C/12T, Intel Core i7-8700, 6C/12T"


def test_extract_agent_details_single_cpu_model_string():
    """A plain string cpu_model should be passed through unchanged."""
    agent = {
        "hostname": "SERVER-01",
        "agent_id": "def456",
        "cpu_model": "AMD EPYC 7302",
        "operating_system": "Ubuntu 22.04",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["cpu_name"] == "AMD EPYC 7302"


def test_extract_agent_details_uses_logged_in_username():
    """logged_in_username is the underlying model field – it should map to last_user."""
    agent = {
        "hostname": "PC-ALICE",
        "agent_id": "ghi789",
        "logged_in_username": "alice",
        "operating_system": "Windows 11",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["last_user"] == "alice"


def test_extract_agent_details_uses_logged_username_computed_field():
    """logged_username is the serialiser-computed field returned by the list endpoint."""
    agent = {
        "hostname": "PC-BOB",
        "agent_id": "jkl012",
        "logged_username": "bob",
        "operating_system": "Windows 10",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["last_user"] == "bob"


def test_extract_agent_details_ignores_placeholder_username():
    """The sentinel value '-' emitted by the serialiser means no user is logged in."""
    agent = {
        "hostname": "PC-EMPTY",
        "agent_id": "mno345",
        "logged_username": "-",
        "operating_system": "Windows 10",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["last_user"] is None


def test_extract_agent_details_ignores_none_string_username():
    """logged_in_username can hold the literal string 'None' in TacticalRMM."""
    agent = {
        "hostname": "PC-NONE",
        "agent_id": "pqr678",
        "logged_in_username": "None",
        "operating_system": "Windows 10",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["last_user"] is None


def test_extract_agent_details_joins_physical_disks():
    """physical_disks is a list in the real TacticalRMM API – entries should be joined."""
    agent = {
        "hostname": "SERVER-DISK",
        "agent_id": "stu901",
        "physical_disks": [
            "WDC WD10EZEX-08WN4A0 931GB SATA",
            "Samsung SSD 860 EVO 500GB SATA",
        ],
        "operating_system": "Windows Server 2019",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["hdd_size"] == "WDC WD10EZEX-08WN4A0 931GB SATA | Samsung SSD 860 EVO 500GB SATA"


def test_extract_agent_details_prefers_total_disk_over_physical_disks():
    """total_disk should take precedence over physical_disks when both are present."""
    agent = {
        "hostname": "SERVER-PRIO",
        "agent_id": "vwx234",
        "total_disk": "2 TB",
        "physical_disks": ["Some Disk 1TB SATA"],
        "operating_system": "Windows Server 2022",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["hdd_size"] == "2 TB"


def test_extract_agent_details_total_ram_field():
    """total_ram is the real TacticalRMM model field (in MB) – it should map to ram_gb."""
    agent = {
        "hostname": "SERVER-RAM",
        "agent_id": "yz0123",
        "total_ram": 32768,  # 32 GB in MB
        "operating_system": "Windows Server 2022",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["ram_gb"] == pytest.approx(32.0)


def test_extract_agent_details_full_table_serialiser_payload():
    """Simulate the full AgentTableSerializer payload from /agents/ endpoint."""
    agent = {
        "agent_id": "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz",
        "hostname": "BJP-LAB-ACQ1",
        "monitoring_type": "workstation",
        "operating_system": "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)",
        "serial_number": "SN-ABC-12345",
        "cpu_model": ["Intel Core i7-8700, 6C/12T"],
        "total_ram": 16384,
        "physical_disks": ["WDC WD10EZEX-08WN4A0 931GB SATA"],
        "last_seen": "2025-11-04T02:40:20.840228Z",
        "status": "online",
        "logged_username": "jdoe",
        "make_model": "Dell OptiPlex 7090",
        "public_ip": "1.2.3.4",
        "plat": "windows",
        "site_name": "Head Office",
        "client_name": "Acme Corp",
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["name"] == "BJP-LAB-ACQ1"
    assert details["type"] == "workstation"
    assert details["serial_number"] == "SN-ABC-12345"
    assert details["os_name"] == "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)"
    assert details["cpu_name"] == "Intel Core i7-8700, 6C/12T"
    assert details["ram_gb"] == pytest.approx(16.0)
    assert details["hdd_size"] == "WDC WD10EZEX-08WN4A0 931GB SATA"
    assert details["last_sync"] == "2025-11-04T02:40:20.840228Z"
    assert details["status"] == "online"
    assert details["last_user"] == "jdoe"
    assert details["tactical_asset_id"] == "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz"
    assert details["site_name"] == "Head Office"
    assert details["client_name"] == "Acme Corp"


# Tests for fetch_clients function

def test_fetch_clients_handles_list_response(monkeypatch):
    """Test that fetch_clients correctly parses a list response from /beta/v1/client."""
    # Mock the _call_endpoint function to return a list of clients
    async def fake_call_endpoint(endpoint: str):
        return [
            {
                "id": 1,
                "name": "Acme Corporation",
                "created_time": "2025-11-06T12:10:17.155Z",
                "modified_time": "2025-11-06T12:10:17.155Z",
            },
            {
                "id": 2,
                "name": "Beta Industries",
                "created_time": "2025-11-05T10:00:00.000Z",
                "modified_time": "2025-11-05T10:00:00.000Z",
            },
        ]
    
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    
    # Need to also mock _load_settings since fetch_clients calls it
    async def fake_load_settings():
        return {
            "base_url": "https://api.example.com",
            "api_key": "test-key",
            "verify_ssl": True,
        }
    
    monkeypatch.setattr(tacticalrmm, "_load_settings", fake_load_settings)
    
    # Run the test
    import asyncio
    clients = asyncio.run(tacticalrmm.fetch_clients())
    
    assert len(clients) == 2
    assert clients[0]["id"] == 1
    assert clients[0]["name"] == "Acme Corporation"
    assert clients[1]["id"] == 2
    assert clients[1]["name"] == "Beta Industries"


def test_fetch_clients_handles_paginated_response(monkeypatch):
    """Test that fetch_clients correctly parses a paginated response with 'results' key."""
    # Mock the _call_endpoint function to return a paginated response
    async def fake_call_endpoint(endpoint: str):
        return {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": 10,
                    "name": "Company One",
                },
                {
                    "id": 20,
                    "name": "Company Two",
                },
            ],
        }
    
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    
    # Mock _load_settings
    async def fake_load_settings():
        return {
            "base_url": "https://api.example.com",
            "api_key": "test-key",
            "verify_ssl": True,
        }
    
    monkeypatch.setattr(tacticalrmm, "_load_settings", fake_load_settings)
    
    # Run the test
    import asyncio
    clients = asyncio.run(tacticalrmm.fetch_clients())
    
    assert len(clients) == 2
    assert clients[0]["id"] == 10
    assert clients[0]["name"] == "Company One"
    assert clients[1]["id"] == 20
    assert clients[1]["name"] == "Company Two"


def test_fetch_clients_handles_single_client_response(monkeypatch):
    """Test that fetch_clients correctly parses a single client object response."""
    # Mock the _call_endpoint function to return a single client
    async def fake_call_endpoint(endpoint: str):
        return {
            "id": 5,
            "name": "Solo Company",
            "created_time": "2025-11-06T12:10:17.155Z",
        }
    
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    
    # Mock _load_settings
    async def fake_load_settings():
        return {
            "base_url": "https://api.example.com",
            "api_key": "test-key",
            "verify_ssl": True,
        }
    
    monkeypatch.setattr(tacticalrmm, "_load_settings", fake_load_settings)
    
    # Run the test
    import asyncio
    clients = asyncio.run(tacticalrmm.fetch_clients())
    
    assert len(clients) == 1
    assert clients[0]["id"] == 5
    assert clients[0]["name"] == "Solo Company"


def test_fetch_clients_handles_api_error(monkeypatch):
    """Test that fetch_clients returns empty list on API error."""
    # Mock the _call_endpoint function to raise an error
    async def fake_call_endpoint(endpoint: str):
        raise tacticalrmm.TacticalRMMAPIError("API request failed")
    
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    
    # Mock _load_settings
    async def fake_load_settings():
        return {
            "base_url": "https://api.example.com",
            "api_key": "test-key",
            "verify_ssl": True,
        }
    
    monkeypatch.setattr(tacticalrmm, "_load_settings", fake_load_settings)
    
    # Run the test
    import asyncio
    clients = asyncio.run(tacticalrmm.fetch_clients())
    
    assert len(clients) == 0
