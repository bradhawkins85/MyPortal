"""Tests for company name normalization with various edge cases."""
import pytest

from app.services.company_id_lookup import _normalize_company_name


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_normalize_company_name_basic():
    """Test basic normalization (case and whitespace)."""
    assert _normalize_company_name("Acme Corporation") == "acme corporation"
    assert _normalize_company_name("ACME CORPORATION") == "acme corporation"
    assert _normalize_company_name("acme corporation") == "acme corporation"


def test_normalize_company_name_leading_trailing_whitespace():
    """Test removal of leading and trailing whitespace."""
    assert _normalize_company_name("  Acme Corporation  ") == "acme corporation"
    assert _normalize_company_name("\tAcme Corporation\t") == "acme corporation"
    assert _normalize_company_name("\n Acme Corporation \n") == "acme corporation"


def test_normalize_company_name_multiple_spaces():
    """Test normalization of multiple consecutive spaces."""
    assert _normalize_company_name("Acme  Corporation") == "acme corporation"
    assert _normalize_company_name("Acme   Corporation") == "acme corporation"
    assert _normalize_company_name("Acme    Corporation") == "acme corporation"


def test_normalize_company_name_non_breaking_space():
    """Test normalization of non-breaking spaces (\\xa0)."""
    assert _normalize_company_name("Acme\xa0Corporation") == "acme corporation"
    assert _normalize_company_name("Acme\xa0\xa0Corporation") == "acme corporation"


def test_normalize_company_name_tabs():
    """Test normalization of tabs."""
    assert _normalize_company_name("Acme\tCorporation") == "acme corporation"
    assert _normalize_company_name("Acme\t\tCorporation") == "acme corporation"


def test_normalize_company_name_mixed_whitespace():
    """Test normalization of mixed whitespace characters."""
    assert _normalize_company_name("Acme \t Corporation") == "acme corporation"
    assert _normalize_company_name("Acme\xa0 \tCorporation") == "acme corporation"


def test_normalize_company_name_zero_width_space():
    """Test removal of zero-width spaces (\\u200b)."""
    assert _normalize_company_name("Test Company\u200b") == "test company"
    assert _normalize_company_name("Test\u200bCompany") == "testcompany"


def test_normalize_company_name_empty():
    """Test normalization of empty and None values."""
    assert _normalize_company_name("") == ""
    assert _normalize_company_name(None) == ""
    assert _normalize_company_name("   ") == ""


def test_normalize_company_name_preserves_punctuation():
    """Test that punctuation is preserved."""
    assert _normalize_company_name("Smith & Co.") == "smith & co."
    assert _normalize_company_name("ABC-123") == "abc-123"
    assert _normalize_company_name("Test (Pty) Ltd") == "test (pty) ltd"


def test_normalize_company_name_unicode_normalization():
    """Test Unicode normalization (NFKC)."""
    # Some Unicode characters have multiple representations
    # NFKC normalization ensures they're represented consistently
    # Example: café can be represented as café (single char) or café (combining char)
    assert _normalize_company_name("café") == _normalize_company_name("café")


@pytest.mark.anyio
async def test_tactical_lookup_with_whitespace_variations(monkeypatch):
    """Test that Tactical RMM lookup works with whitespace variations."""
    from app.services import company_id_lookup
    
    async def fake_fetch_clients():
        return [
            {"id": "1", "name": "Acme  Corporation"},  # Double space
            {"id": "2", "name": "Beta\tIndustries"},    # Tab
            {"id": "3", "name": "Gamma\xa0Tech"},       # Non-breaking space
        ]
    
    monkeypatch.setattr(company_id_lookup.tacticalrmm, "fetch_clients", fake_fetch_clients)
    
    # All of these should find the client even with different whitespace
    result1 = await company_id_lookup._lookup_tactical_client_id("Acme Corporation")
    assert result1 == "1"
    
    result2 = await company_id_lookup._lookup_tactical_client_id("Beta Industries")
    assert result2 == "2"
    
    result3 = await company_id_lookup._lookup_tactical_client_id("Gamma Tech")
    assert result3 == "3"


@pytest.mark.anyio
async def test_syncro_lookup_with_whitespace_variations(monkeypatch):
    """Test that Syncro lookup works with whitespace variations."""
    from app.services import company_id_lookup
    
    async def fake_list_customers(*, page: int, per_page: int):
        if page == 1:
            return (
                [
                    {"id": "100", "business_name": "Delta  Systems"},  # Double space
                    {"id": "200", "name": "Epsilon\tCorp"},            # Tab
                ],
                {"total_pages": 1},
            )
        return ([], {"total_pages": 1})
    
    monkeypatch.setattr(company_id_lookup.syncro, "list_customers", fake_list_customers)
    
    # Should find the customer even with different whitespace
    result1 = await company_id_lookup._lookup_syncro_company_id("Delta Systems")
    assert result1 == "100"
    
    result2 = await company_id_lookup._lookup_syncro_company_id("Epsilon Corp")
    assert result2 == "200"


@pytest.mark.anyio
async def test_xero_lookup_with_whitespace_variations(monkeypatch):
    """Test that Xero lookup works with whitespace variations."""
    from app.services import company_id_lookup
    
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
                    {"ContactID": "xero-1", "Name": "Zeta  Industries"},  # Double space
                    {"ContactID": "xero-2", "Name": "Theta\xa0Corp"},     # Non-breaking space
                ]
            })
    
    monkeypatch.setattr(company_id_lookup.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(company_id_lookup.modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr(company_id_lookup.httpx, "AsyncClient", FakeClient)
    
    # Should find the contact even with different whitespace
    result1 = await company_id_lookup._lookup_xero_contact_id("Zeta Industries")
    assert result1 == "xero-1"
    
    result2 = await company_id_lookup._lookup_xero_contact_id("Theta Corp")
    assert result2 == "xero-2"
