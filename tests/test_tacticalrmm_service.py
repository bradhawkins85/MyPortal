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


# Tests for fetch_clients function

def test_fetch_clients_handles_list_response(monkeypatch):
    """Test that fetch_clients correctly parses a list response from /beta/v1/client."""
    import pytest
    
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
    import pytest
    
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
    import pytest
    
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
    import pytest
    
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
