"""Test issue slug count and list variables in dynamic_variables service."""
import pytest

from app.services import dynamic_variables, value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_extract_issue_count_requests():
    """Test extracting count:issue:slug tokens."""
    tokens = ["count:issue:network-outage", "count:issue:printer-issue"]
    requests = dynamic_variables._extract_issue_count_requests(tokens)
    
    assert requests == {
        "count:issue:network-outage": "network-outage",
        "count:issue:printer-issue": "printer-issue",
    }


def test_extract_issue_count_requests_mixed_case():
    """Test that both uppercase and lowercase variants work."""
    tokens = [
        "count:issue:network-outage",
        "COUNT:ISSUE:PRINTER",
        "Count:Issue:Scanner",
    ]
    requests = dynamic_variables._extract_issue_count_requests(tokens)
    
    # Should preserve original case in token key but extract slug correctly
    assert "count:issue:network-outage" in requests
    assert "COUNT:ISSUE:PRINTER" in requests
    assert "Count:Issue:Scanner" in requests
    assert requests["count:issue:network-outage"] == "network-outage"
    assert requests["COUNT:ISSUE:PRINTER"] == "PRINTER"


def test_extract_issue_count_requests_ignores_invalid():
    """Test that invalid tokens are ignored."""
    tokens = [
        "count:issue:valid",
        "count:issue",  # Missing slug
        "count:something",  # Not issue
        "count:asset:field",  # Different pattern
        "count:issue:",  # Empty slug
        "",  # Empty string
    ]
    requests = dynamic_variables._extract_issue_count_requests(tokens)
    
    # Only the valid one should be extracted
    assert requests == {"count:issue:valid": "valid"}


def test_extract_issue_list_requests():
    """Test extracting list:issue:slug tokens."""
    tokens = ["list:issue:network-outage", "list:issue:printer-issue"]
    requests = dynamic_variables._extract_issue_list_requests(tokens)
    
    assert requests == {
        "list:issue:network-outage": "network-outage",
        "list:issue:printer-issue": "printer-issue",
    }


def test_extract_issue_list_requests_mixed_case():
    """Test that both uppercase and lowercase variants work."""
    tokens = [
        "list:issue:network-outage",
        "LIST:ISSUE:PRINTER",
        "List:Issue:Scanner",
    ]
    requests = dynamic_variables._extract_issue_list_requests(tokens)
    
    # Should preserve original case in token key but extract slug correctly
    assert "list:issue:network-outage" in requests
    assert "LIST:ISSUE:PRINTER" in requests
    assert "List:Issue:Scanner" in requests
    assert requests["list:issue:network-outage"] == "network-outage"
    assert requests["LIST:ISSUE:PRINTER"] == "PRINTER"


def test_extract_issue_list_requests_ignores_invalid():
    """Test that invalid tokens are ignored."""
    tokens = [
        "list:issue:valid",
        "list:issue",  # Missing slug
        "list:something",  # Not issue
        "list:asset:field",  # Different pattern
        "list:issue:",  # Empty slug
        "",  # Empty string
    ]
    requests = dynamic_variables._extract_issue_list_requests(tokens)
    
    # Only the valid one should be extracted
    assert requests == {"list:issue:valid": "valid"}


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_issue_counts(monkeypatch):
    """Test that issue counts are included in the token map."""
    # Mock the repository function
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        # Return different counts for different slugs
        counts = {
            "network-outage": 12,
            "printer-issue": 5,
        }
        return counts.get(issue_slug, 0)
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    tokens = ["count:issue:network-outage", "count:issue:printer-issue"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:issue:network-outage"] == "12"
    assert result["count:issue:printer-issue"] == "5"


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_issue_lists(monkeypatch):
    """Test that issue asset lists are included in the token map."""
    # Mock the repository function
    async def fake_list_assets_by_issue_slug(company_id=None, issue_slug=None):
        # Return different lists for different slugs
        lists = {
            "network-outage": ["Server01", "Router01", "Switch01"],
            "printer-issue": ["Printer01", "Printer02"],
        }
        return lists.get(issue_slug, [])
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "list_assets_by_issue_slug",
        fake_list_assets_by_issue_slug,
    )
    
    tokens = ["list:issue:network-outage", "list:issue:printer-issue"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["list:issue:network-outage"] == "Server01, Router01, Switch01"
    assert result["list:issue:printer-issue"] == "Printer01, Printer02"


@pytest.mark.anyio
async def test_build_dynamic_token_map_with_mixed_patterns(monkeypatch):
    """Test that asset custom fields and issue variables work together."""
    # Mock asset custom field count
    async def fake_count_assets_by_custom_field(company_id=None, field_name=None, field_value=True):
        return 10
    
    # Mock issue count
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        return 15
    
    monkeypatch.setattr(
        dynamic_variables.asset_custom_fields_repo,
        "count_assets_by_custom_field",
        fake_count_assets_by_custom_field,
    )
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    tokens = ["count:asset:bitdefender", "count:issue:network-outage"]
    context = {"company_id": 42}
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:asset:bitdefender"] == "10"
    assert result["count:issue:network-outage"] == "15"


@pytest.mark.anyio
async def test_render_string_async_with_issue_count(monkeypatch):
    """Test that issue counts work in template rendering."""
    # Mock the repository function
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        return 8
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    template = "You have {{count:issue:network-outage}} assets affected by network outage."
    context = {"company_id": 5}
    
    result = await value_templates.render_string_async(template, context)
    
    assert result == "You have 8 assets affected by network outage."


@pytest.mark.anyio
async def test_render_string_async_with_issue_list(monkeypatch):
    """Test that issue asset lists work in template rendering."""
    # Mock the repository function
    async def fake_list_assets_by_issue_slug(company_id=None, issue_slug=None):
        return ["Server01", "Router01", "Switch01"]
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "list_assets_by_issue_slug",
        fake_list_assets_by_issue_slug,
    )
    
    template = "Affected assets: {{list:issue:network-outage}}"
    context = {"company_id": 5}
    
    result = await value_templates.render_string_async(template, context)
    
    assert result == "Affected assets: Server01, Router01, Switch01"


@pytest.mark.anyio
async def test_render_value_async_with_issue_counts(monkeypatch):
    """Test that issue counts work in complex payload rendering."""
    # Mock the repository function
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        counts = {
            "network-outage": 12,
            "printer-issue": 5,
            "vpn-problem": 3,
        }
        return counts.get(issue_slug, 0)
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    payload = {
        "subject": "Issue Report",
        "network_count": "{{count:issue:network-outage}}",
        "printer_count": "{{count:issue:printer-issue}}",
        "vpn_count": "{{count:issue:vpn-problem}}",
        "summary": "Network: {{count:issue:network-outage}}, Printer: {{count:issue:printer-issue}}, VPN: {{count:issue:vpn-problem}}",
    }
    context = {"company_id": 10}
    
    result = await value_templates.render_value_async(payload, context)
    
    assert result["subject"] == "Issue Report"
    assert result["network_count"] == "12"
    assert result["printer_count"] == "5"
    assert result["vpn_count"] == "3"
    assert result["summary"] == "Network: 12, Printer: 5, VPN: 3"


@pytest.mark.anyio
async def test_issue_count_with_no_company_context(monkeypatch):
    """Test that issue counts work without a specific company."""
    # Mock the repository function
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        # When company_id is None, should count across all companies
        assert company_id is None
        return 25
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    tokens = ["count:issue:network-outage"]
    context = {}  # No company_id
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:issue:network-outage"] == "25"


@pytest.mark.anyio
async def test_issue_count_extracts_company_from_ticket(monkeypatch):
    """Test that company_id is extracted from ticket context."""
    call_args = []
    
    async def fake_count_assets_by_issue_slug(company_id=None, issue_slug=None):
        call_args.append({"company_id": company_id, "issue_slug": issue_slug})
        return 7
    
    monkeypatch.setattr(
        dynamic_variables.issues_repo,
        "count_assets_by_issue_slug",
        fake_count_assets_by_issue_slug,
    )
    
    tokens = ["count:issue:network-outage"]
    context = {
        "ticket": {
            "id": 123,
            "company_id": 99,
        }
    }
    
    result = await dynamic_variables.build_dynamic_token_map(tokens, context)
    
    assert result["count:issue:network-outage"] == "7"
    assert len(call_args) == 1
    assert call_args[0]["company_id"] == 99
    assert call_args[0]["issue_slug"] == "network-outage"
