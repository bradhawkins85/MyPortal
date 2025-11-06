"""Test asset custom field count variables in dynamic_variables service."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import dynamic_variables, value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_extract_asset_custom_field_count_requests():
    """Test extracting count:asset:field-name tokens."""
    # Test basic extraction
    tokens = ["count:asset:bitdefender", "count:asset:threatlocker-installed"]
    requests = dynamic_variables._extract_asset_custom_field_count_requests(tokens)
    
    assert requests == {
        "count:asset:bitdefender": "bitdefender",
        "count:asset:threatlocker-installed": "threatlocker-installed",
    }


def test_extract_asset_custom_field_count_requests_mixed_case():
    """Test that both uppercase and lowercase variants work."""
    tokens = [
        "count:asset:bitdefender",
        "COUNT:ASSET:THREATLOCKER",
        "Count:Asset:Webroot",
    ]
    requests = dynamic_variables._extract_asset_custom_field_count_requests(tokens)
    
    # Should preserve original case in token key but extract field name correctly
    assert "count:asset:bitdefender" in requests
    assert "COUNT:ASSET:THREATLOCKER" in requests
    assert "Count:Asset:Webroot" in requests
    assert requests["count:asset:bitdefender"] == "bitdefender"
    assert requests["COUNT:ASSET:THREATLOCKER"] == "THREATLOCKER"


def test_extract_asset_custom_field_count_requests_ignores_invalid():
    """Test that invalid tokens are ignored."""
    tokens = [
        "count:asset:valid",
        "count:asset",  # Missing field name
        "count:something",  # Not asset
        "active_assets",  # Different pattern
        "count:asset:",  # Empty field name
        "",  # Empty string
    ]
    requests = dynamic_variables._extract_asset_custom_field_count_requests(tokens)
    
    # Only the valid one should be extracted
    assert requests == {"count:asset:valid": "valid"}


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_custom_fields(monkeypatch):
    """Test that custom field counts are included in the token map."""
    # Mock the repository function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        # Return different counts for different field names
        counts = {
            "bitdefender": 15,
            "threatlocker-installed": 8,
        }
        return counts.get(field_name, 0)
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    tokens = ["count:asset:bitdefender", "count:asset:threatlocker-installed"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "15"
    assert result["count:asset:threatlocker-installed"] == "8"


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_both_patterns(monkeypatch):
    """Test that both active assets and custom field counts work together."""
    # Mock active assets count
    async def fake_count_active_assets(*, company_id=None, since=None):
        return 25
    
    # Mock custom field count
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 10
    
    monkeypatch.setattr(
        dynamic_variables.assets_repo,
        "count_active_assets",
        fake_count_active_assets,
    )
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    tokens = ["ACTIVE_ASSETS", "count:asset:bitdefender"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["ACTIVE_ASSETS"] == "25"
    assert result["count:asset:bitdefender"] == "10"


@pytest.mark.anyio
async def test_render_string_async_with_custom_field_count(monkeypatch):
    """Test that custom field counts work in template rendering."""
    # Mock the repository function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 12
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    template = "You have {{count:asset:bitdefender}} assets with Bitdefender installed."
    context = {"company_id": 5}
    
    result = await value_templates.render_string_async(template, context)
    
    assert result == "You have 12 assets with Bitdefender installed."


@pytest.mark.anyio
async def test_render_value_async_with_custom_field_counts(monkeypatch):
    """Test that custom field counts work in complex payload rendering."""
    # Mock the repository function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        counts = {
            "bitdefender": 15,
            "threatlocker-installed": 8,
            "webroot": 3,
        }
        return counts.get(field_name, 0)
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    payload = {
        "subject": "Security Software Report",
        "bitdefender_count": "{{count:asset:bitdefender}}",
        "threatlocker_count": "{{count:asset:threatlocker-installed}}",
        "webroot_count": "{{count:asset:webroot}}",
        "summary": "Bitdefender: {{count:asset:bitdefender}}, ThreatLocker: {{count:asset:threatlocker-installed}}, Webroot: {{count:asset:webroot}}",
    }
    context = {"company_id": 10}
    
    result = await value_templates.render_value_async(payload, context)
    
    assert result["subject"] == "Security Software Report"
    assert result["bitdefender_count"] == "15"
    assert result["threatlocker_count"] == "8"
    assert result["webroot_count"] == "3"
    assert result["summary"] == "Bitdefender: 15, ThreatLocker: 8, Webroot: 3"


@pytest.mark.anyio
async def test_custom_field_count_with_no_company_context(monkeypatch):
    """Test that custom field counts work without a specific company."""
    # Mock the repository function
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        # When company_id is None, should count across all companies
        assert company_id is None
        return 50
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    tokens = ["count:asset:bitdefender"]
    context = {}  # No company_id
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "50"


@pytest.mark.anyio
async def test_custom_field_count_extracts_company_from_ticket(monkeypatch):
    """Test that company_id is extracted from ticket context."""
    call_args = []
    
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        call_args.append({"company_id": company_id, "field_name": field_name})
        return 7
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    
    tokens = ["count:asset:bitdefender"]
    context = {
        "ticket": {
            "id": 123,
            "company_id": 99,
        }
    }
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "7"
    assert len(call_args) == 1
    assert call_args[0]["company_id"] == 99
    assert call_args[0]["field_name"] == "bitdefender"
