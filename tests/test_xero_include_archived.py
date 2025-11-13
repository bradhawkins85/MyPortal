"""Test that Xero contact lookup includes archived contacts (e.g., Demo Company)."""

import pytest

from app.services import company_id_lookup


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_lookup_xero_contact_includes_archived_contacts(monkeypatch):
    """Test that Xero contact lookup includes archived contacts like Demo Company.
    
    This test verifies that the includeArchived=true parameter is passed to the
    Xero API when searching for contacts. Without this parameter, archived contacts
    (such as Demo Company) will not be returned by the API.
    """
    # Track what parameters were passed to the API
    api_calls = []
    
    async def fake_get_module(slug: str, *, redact: bool = True):
        if slug == "xero":
            return {
                "enabled": True,
                "settings": {"tenant_id": "tenant-123"},
            }
        return None
    
    async def fake_acquire_xero_access_token():
        return "fake-access-token"
    
    class FakeResponse:
        def __init__(self, json_data):
            self._json_data = json_data
            self.status_code = 200
        
        def json(self):
            return self._json_data
        
        def raise_for_status(self):
            pass
    
    class FakeClient:
        def __init__(self, timeout):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        
        async def get(self, url, headers=None, params=None):
            # Record the API call parameters
            api_calls.append({
                "url": url,
                "headers": headers,
                "params": params,
            })
            
            # Simulate Xero API returning archived Demo Company
            return FakeResponse({
                "Contacts": [
                    {
                        "ContactID": "demo-company-id",
                        "Name": "Demo Company",
                        "ContactStatus": "ARCHIVED",
                    },
                ]
            })
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    result = await company_id_lookup._lookup_xero_contact_id("Demo Company")
    
    # Verify the contact was found
    assert result == "demo-company-id"
    
    # Verify that includeArchived=true was passed in the API call
    assert len(api_calls) == 1
    assert api_calls[0]["params"] is not None
    assert api_calls[0]["params"].get("includeArchived") == "true"


@pytest.mark.anyio
async def test_lookup_xero_contact_finds_archived_demo_company(monkeypatch):
    """Test that lookup can find Demo Company which is typically archived in Xero."""
    async def fake_get_module(slug: str, *, redact: bool = True):
        if slug == "xero":
            return {
                "enabled": True,
                "settings": {"tenant_id": "tenant-123"},
            }
        return None
    
    async def fake_acquire_xero_access_token():
        return "fake-access-token"
    
    class FakeResponse:
        def __init__(self, json_data):
            self._json_data = json_data
            self.status_code = 200
        
        def json(self):
            return self._json_data
        
        def raise_for_status(self):
            pass
    
    class FakeClient:
        def __init__(self, timeout):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        
        async def get(self, url, headers=None, params=None):
            # Return both active and archived contacts
            return FakeResponse({
                "Contacts": [
                    {"ContactID": "active-123", "Name": "Active Company"},
                    {"ContactID": "demo-456", "Name": "Demo Company"},  # Archived
                    {"ContactID": "active-789", "Name": "Another Active Company"},
                ]
            })
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    # Search for Demo Company (case-insensitive)
    result = await company_id_lookup._lookup_xero_contact_id("demo company")
    
    assert result == "demo-456"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_finds_archived_xero_contact(monkeypatch):
    """Test that full lookup process can find archived Xero contacts."""
    companies = [
        {
            "id": 1,
            "name": "Demo Company",
            "syncro_company_id": "syncro-123",
            "tacticalrmm_client_id": "tactical-456",
            "xero_id": None,
        }
    ]
    
    async def fake_get_company_by_id(company_id: int):
        for company in companies:
            if company["id"] == company_id:
                return dict(company)
        return None
    
    async def fake_update_company(company_id: int, **updates):
        for company in companies:
            if company["id"] == company_id:
                company.update(updates)
                return dict(company)
        raise ValueError("Company not found")
    
    async def fake_get_module(slug: str, *, redact: bool = True):
        if slug == "xero":
            return {
                "enabled": True,
                "settings": {"tenant_id": "tenant-123"},
            }
        return None
    
    async def fake_acquire_xero_access_token():
        return "fake-access-token"
    
    class FakeResponse:
        def __init__(self, json_data):
            self._json_data = json_data
            self.status_code = 200
        
        def json(self):
            return self._json_data
        
        def raise_for_status(self):
            pass
    
    class FakeClient:
        def __init__(self, timeout):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        
        async def get(self, url, headers=None, params=None):
            # Return Demo Company as an archived contact
            if params and params.get("includeArchived") == "true":
                return FakeResponse({
                    "Contacts": [
                        {"ContactID": "demo-xero-id", "Name": "Demo Company"},
                    ]
                })
            # Without includeArchived, return empty
            return FakeResponse({"Contacts": []})
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "updated"
    assert result["xero_lookup"] == "found"
    assert result["updates"]["xero_id"] == "demo-xero-id"
    assert companies[0]["xero_id"] == "demo-xero-id"
