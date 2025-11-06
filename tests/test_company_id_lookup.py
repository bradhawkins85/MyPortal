import pytest

from app.services import company_id_lookup


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_finds_syncro_id(monkeypatch):
    """Test that lookup finds a missing Syncro company ID."""
    companies = [
        {
            "id": 1,
            "name": "Acme Corporation",
            "syncro_company_id": None,
            "tacticalrmm_client_id": "tactical-123",
            "xero_id": "xero-456",
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
    
    async def fake_list_customers(*, page: int, per_page: int):
        if page == 1:
            return (
                [
                    {"id": "syncro-100", "business_name": "Acme Corporation"},
                    {"id": "syncro-200", "name": "Other Company"},
                ],
                {"total_pages": 1},
            )
        return ([], {"total_pages": 1})
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "updated"
    assert result["syncro_lookup"] == "found"
    assert result["updates"]["syncro_company_id"] == "syncro-100"
    assert companies[0]["syncro_company_id"] == "syncro-100"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_finds_tactical_id(monkeypatch):
    """Test that lookup finds a missing Tactical RMM client ID."""
    companies = [
        {
            "id": 1,
            "name": "Beta Industries",
            "syncro_company_id": "syncro-123",
            "tacticalrmm_client_id": None,
            "xero_id": "xero-456",
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
    
    async def fake_fetch_clients():
        return [
            {"id": "client-100", "name": "Beta Industries"},
            {"id": "client-200", "name": "Other Client"},
        ]
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "updated"
    assert result["tactical_lookup"] == "found"
    assert result["updates"]["tacticalrmm_client_id"] == "client-100"
    assert companies[0]["tacticalrmm_client_id"] == "client-100"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_skips_configured_ids(monkeypatch):
    """Test that lookup skips IDs that are already configured."""
    companies = [
        {
            "id": 1,
            "name": "Complete Company",
            "syncro_company_id": "syncro-123",
            "tacticalrmm_client_id": "tactical-456",
            "xero_id": "xero-789",
        }
    ]
    
    async def fake_get_company_by_id(company_id: int):
        for company in companies:
            if company["id"] == company_id:
                return dict(company)
        return None
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "no_updates"
    assert result["syncro_lookup"] == "skipped"
    assert result["tactical_lookup"] == "skipped"
    assert result["xero_lookup"] == "skipped"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_not_found(monkeypatch):
    """Test that lookup handles cases where IDs are not found in external APIs."""
    companies = [
        {
            "id": 1,
            "name": "Unknown Company",
            "syncro_company_id": None,
            "tacticalrmm_client_id": None,
            "xero_id": None,
        }
    ]
    
    async def fake_get_company_by_id(company_id: int):
        for company in companies:
            if company["id"] == company_id:
                return dict(company)
        return None
    
    async def fake_list_customers(*, page: int, per_page: int):
        return ([{"id": "syncro-100", "name": "Other Company"}], {"total_pages": 1})
    
    async def fake_fetch_clients():
        return [
            {"id": "client-100", "name": "Other Client"},
        ]
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "no_updates"
    assert result["syncro_lookup"] == "not_found"
    assert result["tactical_lookup"] == "not_found"


@pytest.mark.anyio
async def test_lookup_syncro_company_id_case_insensitive(monkeypatch):
    """Test that Syncro company lookup is case insensitive."""
    async def fake_list_customers(*, page: int, per_page: int):
        if page == 1:
            return (
                [
                    {"id": "syncro-100", "business_name": "ACME CORPORATION"},
                ],
                {"total_pages": 1},
            )
        return ([], {"total_pages": 1})
    
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    
    result = await company_id_lookup._lookup_syncro_company_id("acme corporation")
    
    assert result == "syncro-100"


@pytest.mark.anyio
async def test_lookup_tactical_client_id_case_insensitive(monkeypatch):
    """Test that Tactical RMM client lookup is case insensitive."""
    async def fake_fetch_clients():
        return [
            {"id": "client-100", "name": "BETA INDUSTRIES"},
        ]
    
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    result = await company_id_lookup._lookup_tactical_client_id("beta industries")
    
    assert result == "client-100"


@pytest.mark.anyio
async def test_refresh_all_missing_company_ids(monkeypatch):
    """Test that refresh processes all companies with missing IDs."""
    companies = [
        {
            "id": 1,
            "name": "Company A",
            "syncro_company_id": None,
            "tacticalrmm_client_id": None,
            "xero_id": None,
        },
        {
            "id": 2,
            "name": "Company B",
            "syncro_company_id": "syncro-200",
            "tacticalrmm_client_id": "tactical-200",
            "xero_id": "xero-200",
        },
        {
            "id": 3,
            "name": "Company C",
            "syncro_company_id": None,
            "tacticalrmm_client_id": "tactical-300",
            "xero_id": None,
        },
    ]
    
    async def fake_list_companies():
        return companies
    
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
    
    async def fake_list_customers(*, page: int, per_page: int):
        return (
            [
                {"id": "syncro-100", "name": "Company A"},
                {"id": "syncro-300", "name": "Company C"},
            ],
            {"total_pages": 1},
        )
    
    async def fake_fetch_clients():
        return [
            {"id": "client-100", "name": "Company A"},
        ]
    
    monkeypatch.setattr(company_id_lookup.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    result = await company_id_lookup.refresh_all_missing_company_ids()
    
    assert result["total_companies"] == 3
    assert result["processed"] == 2  # Company 1 and 3 (Company 2 has all IDs)
    assert result["skipped"] == 1  # Company 2
    assert result["updated"] == 2  # Company 1 and 3 got updates


@pytest.mark.anyio
async def test_lookup_handles_syncro_configuration_error(monkeypatch):
    """Test that lookup gracefully handles Syncro configuration errors."""
    companies = [
        {
            "id": 1,
            "name": "Test Company",
            "syncro_company_id": None,
            "tacticalrmm_client_id": None,
            "xero_id": None,
        }
    ]
    
    async def fake_get_company_by_id(company_id: int):
        for company in companies:
            if company["id"] == company_id:
                return dict(company)
        return None
    
    async def fake_list_customers(*, page: int, per_page: int):
        raise company_id_lookup.syncro.SyncroConfigurationError("Not configured")
    
    async def fake_fetch_clients():
        raise company_id_lookup.tacticalrmm.TacticalRMMConfigurationError("Not configured")
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    # Should handle configuration errors gracefully
    assert result["status"] == "no_updates"
    assert result["syncro_lookup"] == "not_found"
    assert result["tactical_lookup"] == "not_found"


@pytest.mark.anyio
async def test_lookup_missing_company_ids_finds_xero_id(monkeypatch):
    """Test that lookup finds a missing Xero contact ID."""
    companies = [
        {
            "id": 1,
            "name": "Gamma Tech",
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
            if params and params.get("page") == 1:
                return FakeResponse({
                    "Contacts": [
                        {"ContactID": "xero-789", "Name": "Gamma Tech"},
                        {"ContactID": "xero-999", "Name": "Other Company"},
                    ]
                })
            return FakeResponse({"Contacts": []})
    
    monkeypatch.setattr(company_id_lookup.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(company_id_lookup.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    result = await company_id_lookup.lookup_missing_company_ids(1)
    
    assert result["status"] == "updated"
    assert result["xero_lookup"] == "found"
    assert result["updates"]["xero_id"] == "xero-789"
    assert companies[0]["xero_id"] == "xero-789"


@pytest.mark.anyio
async def test_lookup_xero_contact_id_case_insensitive(monkeypatch):
    """Test that Xero contact lookup is case insensitive."""
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
            return FakeResponse({
                "Contacts": [
                    {"ContactID": "xero-123", "Name": "DELTA SYSTEMS"},
                ]
            })
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    result = await company_id_lookup._lookup_xero_contact_id("delta systems")
    
    assert result == "xero-123"


@pytest.mark.anyio
async def test_lookup_xero_contact_id_not_found(monkeypatch):
    """Test that Xero contact lookup handles not found cases."""
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
            return FakeResponse({
                "Contacts": [
                    {"ContactID": "xero-999", "Name": "Other Company"},
                ]
            })
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    result = await company_id_lookup._lookup_xero_contact_id("Missing Company")
    
    assert result is None


@pytest.mark.anyio
async def test_lookup_xero_contact_id_module_disabled(monkeypatch):
    """Test that Xero contact lookup handles disabled module."""
    async def fake_get_module(slug: str, *, redact: bool = True):
        if slug == "xero":
            return {"enabled": False}
        return None
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    
    result = await company_id_lookup._lookup_xero_contact_id("Test Company")
    
    assert result is None


@pytest.mark.anyio
async def test_lookup_xero_contact_id_missing_tenant(monkeypatch):
    """Test that Xero contact lookup handles missing tenant ID."""
    async def fake_get_module(slug: str, *, redact: bool = True):
        if slug == "xero":
            return {
                "enabled": True,
                "settings": {},
            }
        return None
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    
    result = await company_id_lookup._lookup_xero_contact_id("Test Company")
    
    assert result is None
