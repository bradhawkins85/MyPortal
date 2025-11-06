"""Test asset custom field list variables in dynamic_variables service."""
from unittest.mock import AsyncMock

import pytest

from app.services import dynamic_variables, value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_extract_asset_custom_field_list_requests():
    """Test extracting list:asset:field-name tokens."""
    # Test basic extraction
    tokens = ["list:asset:bitdefender", "list:asset:threatlocker-installed"]
    requests = dynamic_variables._extract_asset_custom_field_list_requests(tokens)
    
    assert requests == {
        "list:asset:bitdefender": "bitdefender",
        "list:asset:threatlocker-installed": "threatlocker-installed",
    }


def test_extract_asset_custom_field_list_requests_mixed_case():
    """Test that both uppercase and lowercase variants work."""
    tokens = [
        "list:asset:bitdefender",
        "LIST:ASSET:THREATLOCKER",
        "List:Asset:Webroot",
    ]
    requests = dynamic_variables._extract_asset_custom_field_list_requests(tokens)
    
    # Should preserve original case in token key but extract field name correctly
    assert "list:asset:bitdefender" in requests
    assert "LIST:ASSET:THREATLOCKER" in requests
    assert "List:Asset:Webroot" in requests
    assert requests["list:asset:bitdefender"] == "bitdefender"
    assert requests["LIST:ASSET:THREATLOCKER"] == "THREATLOCKER"


def test_extract_asset_custom_field_list_requests_ignores_invalid():
    """Test that invalid tokens are ignored."""
    tokens = [
        "list:asset:valid",
        "list:asset",  # Missing field name
        "list:something",  # Not asset
        "active_assets",  # Different pattern
        "list:asset:",  # Empty field name
        "",  # Empty string
    ]
    requests = dynamic_variables._extract_asset_custom_field_list_requests(tokens)
    
    # Only the valid one should be extracted
    assert requests == {"list:asset:valid": "valid"}


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_list_custom_fields(monkeypatch):
    """Test that custom field lists are included in the token map."""
    # Mock the repository function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        # Return different lists for different field names
        lists = {
            "bitdefender": ["Server-01", "Server-02", "Workstation-03"],
            "threatlocker-installed": ["Server-01", "Workstation-01"],
        }
        return lists.get(field_name, [])
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = ["list:asset:bitdefender", "list:asset:threatlocker-installed"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["list:asset:bitdefender"] == "Server-01, Server-02, Workstation-03"
    assert result["list:asset:threatlocker-installed"] == "Server-01, Workstation-01"


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_empty_list(monkeypatch):
    """Test that empty lists return empty string."""
    # Mock the repository function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return []
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = ["list:asset:bitdefender"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["list:asset:bitdefender"] == ""


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_both_count_and_list(monkeypatch):
    """Test that both count and list patterns work together."""
    # Mock count function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 3
    
    # Mock list function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return ["Server-01", "Server-02", "Workstation-03"]
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = ["count:asset:bitdefender", "list:asset:bitdefender"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "3"
    assert result["list:asset:bitdefender"] == "Server-01, Server-02, Workstation-03"


@pytest.mark.anyio
async def test_render_string_async_with_custom_field_list(monkeypatch):
    """Test that custom field lists work in template rendering."""
    # Mock the repository function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return ["Server-01", "Server-02", "Workstation-03"]
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    template = "Assets with Bitdefender: {{list:asset:bitdefender}}"
    context = {"company_id": 5}
    
    result = await value_templates.render_string_async(template, context)
    
    assert result == "Assets with Bitdefender: Server-01, Server-02, Workstation-03"


@pytest.mark.anyio
async def test_render_value_async_with_custom_field_lists(monkeypatch):
    """Test that custom field lists work in complex payload rendering."""
    # Mock the repository function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        lists = {
            "bitdefender": ["Server-01", "Server-02"],
            "threatlocker-installed": ["Workstation-01"],
            "webroot": ["Server-03", "Workstation-02", "Workstation-03"],
        }
        return lists.get(field_name, [])
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    payload = {
        "subject": "Security Software Report",
        "bitdefender_assets": "{{list:asset:bitdefender}}",
        "threatlocker_assets": "{{list:asset:threatlocker-installed}}",
        "webroot_assets": "{{list:asset:webroot}}",
        "summary": "Bitdefender on: {{list:asset:bitdefender}}, ThreatLocker on: {{list:asset:threatlocker-installed}}",
    }
    context = {"company_id": 10}
    
    result = await value_templates.render_value_async(payload, context)
    
    assert result["subject"] == "Security Software Report"
    assert result["bitdefender_assets"] == "Server-01, Server-02"
    assert result["threatlocker_assets"] == "Workstation-01"
    assert result["webroot_assets"] == "Server-03, Workstation-02, Workstation-03"
    assert result["summary"] == "Bitdefender on: Server-01, Server-02, ThreatLocker on: Workstation-01"


@pytest.mark.anyio
async def test_custom_field_list_with_no_company_context(monkeypatch):
    """Test that custom field lists work without a specific company."""
    # Mock the repository function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        # When company_id is None, should list across all companies
        assert company_id is None
        return ["Global-Server-01", "Global-Server-02"]
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = ["list:asset:bitdefender"]
    context = {}  # No company_id
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["list:asset:bitdefender"] == "Global-Server-01, Global-Server-02"


@pytest.mark.anyio
async def test_custom_field_list_extracts_company_from_ticket(monkeypatch):
    """Test that company_id is extracted from ticket context."""
    call_args = []
    
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        call_args.append({"company_id": company_id, "field_name": field_name})
        return ["Server-01", "Server-02"]
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = ["list:asset:bitdefender"]
    context = {
        "ticket": {
            "id": 123,
            "company_id": 99,
        }
    }
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["list:asset:bitdefender"] == "Server-01, Server-02"
    assert len(call_args) == 1
    assert call_args[0]["company_id"] == 99
    assert call_args[0]["field_name"] == "bitdefender"


@pytest.mark.anyio
async def test_list_and_count_different_fields(monkeypatch):
    """Test using list and count on different fields in the same request."""
    # Mock count function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        counts = {
            "bitdefender": 5,
            "webroot": 3,
        }
        return counts.get(field_name, 0)
    
    # Mock list function
    async def fake_list_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        lists = {
            "threatlocker-installed": ["Server-01", "Workstation-01"],
        }
        return lists.get(field_name, [])
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "list_assets_by_custom_field",
        fake_list_assets_by_custom_field,
    )
    
    tokens = [
        "count:asset:bitdefender",
        "count:asset:webroot",
        "list:asset:threatlocker-installed",
    ]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "5"
    assert result["count:asset:webroot"] == "3"
    assert result["list:asset:threatlocker-installed"] == "Server-01, Workstation-01"
