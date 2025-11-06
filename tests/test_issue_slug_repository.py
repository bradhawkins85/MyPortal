"""Test issue slug repository functions."""
import pytest

from app.repositories import issues as issues_repo


class _MockDB:
    def __init__(self, fetch_one_result=None, fetch_all_result=None):
        self.fetch_one_result = fetch_one_result
        self.fetch_all_result = fetch_all_result or []
        self.fetch_one_calls = []
        self.fetch_all_calls = []

    async def fetch_one(self, sql, params):
        self.fetch_one_calls.append((sql.strip(), params))
        return self.fetch_one_result

    async def fetch_all(self, sql, params):
        self.fetch_all_calls.append((sql.strip(), params))
        return self.fetch_all_result


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_issue_by_slug_returns_issue(monkeypatch):
    """Test that get_issue_by_slug returns an issue with the matching slug."""
    row = {
        "issue_id": 5,
        "name": "Network Outage",
        "slug": "network-outage",
        "description": "WAN connectivity unstable",
        "created_by": 1,
        "updated_by": 1,
        "created_at_utc": None,
        "updated_at_utc": None,
    }
    assignment_rows = [
        {
            "assignment_id": 10,
            "issue_id": 5,
            "company_id": 3,
            "status": "investigating",
            "notes": None,
            "updated_by": 2,
            "created_at_utc": None,
            "updated_at_utc": None,
            "company_name": "Acme",
        }
    ]
    
    dummy_db = _MockDB(fetch_one_result=row, fetch_all_result=assignment_rows)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    result = await issues_repo.get_issue_by_slug("network-outage")

    assert result is not None
    assert result["issue_id"] == 5
    assert result["name"] == "Network Outage"
    assert result["slug"] == "network-outage"
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["company_name"] == "Acme"
    # Check that the SQL query used LOWER comparison on slug
    assert len(dummy_db.fetch_one_calls) == 1
    sql, params = dummy_db.fetch_one_calls[0]
    assert "LOWER(slug) = LOWER(%s)" in sql
    assert params == ("network-outage",)


@pytest.mark.anyio
async def test_get_issue_by_slug_case_insensitive(monkeypatch):
    """Test that get_issue_by_slug is case-insensitive."""
    row = {
        "issue_id": 5,
        "name": "Network Outage",
        "slug": "network-outage",
        "description": "WAN connectivity unstable",
        "created_by": 1,
        "updated_by": 1,
        "created_at_utc": None,
        "updated_at_utc": None,
    }
    
    dummy_db = _MockDB(fetch_one_result=row, fetch_all_result=[])
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    # Query with different case
    result = await issues_repo.get_issue_by_slug("NETWORK-OUTAGE")

    assert result is not None
    assert result["slug"] == "network-outage"
    # Verify the parameter was passed as-is (case conversion happens in SQL)
    sql, params = dummy_db.fetch_one_calls[0]
    assert params == ("NETWORK-OUTAGE",)


@pytest.mark.anyio
async def test_get_issue_by_slug_returns_none_when_not_found(monkeypatch):
    """Test that get_issue_by_slug returns None when slug doesn't exist."""
    dummy_db = _MockDB(fetch_one_result=None)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    result = await issues_repo.get_issue_by_slug("nonexistent")

    assert result is None


@pytest.mark.anyio
async def test_count_assets_by_issue_slug_returns_count(monkeypatch):
    """Test counting assets by issue slug."""
    row = {"count": 15}
    dummy_db = _MockDB(fetch_one_result=row)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    count = await issues_repo.count_assets_by_issue_slug(
        company_id=5,
        issue_slug="network-outage",
    )

    assert count == 15
    assert len(dummy_db.fetch_one_calls) == 1
    sql, params = dummy_db.fetch_one_calls[0]
    assert "COUNT(DISTINCT a.id)" in sql
    assert "LOWER(i.slug) = LOWER(%s)" in sql
    assert "a.company_id = %s" in sql
    assert params == ("network-outage", 5)


@pytest.mark.anyio
async def test_count_assets_by_issue_slug_without_company_filter(monkeypatch):
    """Test counting assets by issue slug across all companies."""
    row = {"count": 42}
    dummy_db = _MockDB(fetch_one_result=row)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    count = await issues_repo.count_assets_by_issue_slug(
        company_id=None,
        issue_slug="network-outage",
    )

    assert count == 42
    sql, params = dummy_db.fetch_one_calls[0]
    assert "LOWER(i.slug) = LOWER(%s)" in sql
    # Should not filter by company
    assert "a.company_id = %s" not in sql
    assert params == ("network-outage",)


@pytest.mark.anyio
async def test_count_assets_by_issue_slug_returns_zero_when_no_result(monkeypatch):
    """Test that count returns 0 when no row is returned."""
    dummy_db = _MockDB(fetch_one_result=None)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    count = await issues_repo.count_assets_by_issue_slug(
        company_id=5,
        issue_slug="network-outage",
    )

    assert count == 0


@pytest.mark.anyio
async def test_list_assets_by_issue_slug_returns_asset_names(monkeypatch):
    """Test listing assets by issue slug."""
    rows = [
        {"name": "Server01"},
        {"name": "Router01"},
        {"name": "Switch01"},
    ]
    dummy_db = _MockDB(fetch_all_result=rows)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    assets = await issues_repo.list_assets_by_issue_slug(
        company_id=5,
        issue_slug="network-outage",
    )

    assert assets == ["Server01", "Router01", "Switch01"]
    assert len(dummy_db.fetch_all_calls) == 1
    sql, params = dummy_db.fetch_all_calls[0]
    assert "SELECT DISTINCT a.name" in sql
    assert "LOWER(i.slug) = LOWER(%s)" in sql
    assert "a.company_id = %s" in sql
    assert "ORDER BY a.name ASC" in sql
    assert params == ("network-outage", 5)


@pytest.mark.anyio
async def test_list_assets_by_issue_slug_without_company_filter(monkeypatch):
    """Test listing assets by issue slug across all companies."""
    rows = [
        {"name": "Server01"},
        {"name": "Server02"},
    ]
    dummy_db = _MockDB(fetch_all_result=rows)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    assets = await issues_repo.list_assets_by_issue_slug(
        company_id=None,
        issue_slug="network-outage",
    )

    assert assets == ["Server01", "Server02"]
    sql, params = dummy_db.fetch_all_calls[0]
    assert "LOWER(i.slug) = LOWER(%s)" in sql
    # Should not filter by company
    assert "a.company_id = %s" not in sql
    assert params == ("network-outage",)


@pytest.mark.anyio
async def test_list_assets_by_issue_slug_returns_empty_list_when_no_results(monkeypatch):
    """Test that list returns empty list when no assets found."""
    dummy_db = _MockDB(fetch_all_result=[])
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    assets = await issues_repo.list_assets_by_issue_slug(
        company_id=5,
        issue_slug="network-outage",
    )

    assert assets == []


@pytest.mark.anyio
async def test_list_assets_by_issue_slug_filters_none_names(monkeypatch):
    """Test that None asset names are filtered out."""
    rows = [
        {"name": "Server01"},
        {"name": None},  # This should be filtered
        {"name": "Router01"},
    ]
    dummy_db = _MockDB(fetch_all_result=rows)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    assets = await issues_repo.list_assets_by_issue_slug(
        company_id=5,
        issue_slug="network-outage",
    )

    # Should only include non-None names
    assert assets == ["Server01", "Router01"]
