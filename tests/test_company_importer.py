import pytest

from app.services import company_importer


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_extract_address_collates_parts():
    address = company_importer._extract_address(  # type: ignore[attr-defined]
        {
            "address1": "123 High St",
            "city": "London",
            "state": "Greater London",
            "zip": "E1 6AN",
            "country": "UK",
        }
    )
    assert address == "123 High St, London, Greater London, E1 6AN, UK"


@pytest.mark.anyio
async def test_import_all_companies_creates_and_updates(monkeypatch):
    records: list[dict[str, object]] = [
        {
            "id": 1,
            "name": "Acme Old",
            "address": "Old Street",
            "syncro_company_id": "100",
        }
    ]

    async def fake_get_company_by_syncro_id(syncro_id: str):
        for record in records:
            if record.get("syncro_company_id") == syncro_id:
                return dict(record)
        return None

    async def fake_get_company_by_name(name: str):
        for record in records:
            if str(record.get("name", "")).lower() == name.lower():
                return dict(record)
        return None

    async def fake_create_company(**payload):
        payload.setdefault("id", len(records) + 1)
        records.append(dict(payload))
        return dict(payload)

    async def fake_update_company(company_id: int, **updates):
        for record in records:
            if record.get("id") == company_id:
                record.update(updates)
                return dict(record)
        raise AssertionError("Company not found")

    pages = {
        1: (
            [
                {
                    "id": 100,
                    "business_name": "Acme Corporation",
                    "address1": "10 Downing St",
                    "city": "London",
                },
                {
                    "id": 200,
                    "name": "Beta Industries",
                    "address": "1 Beta Way",
                },
            ],
            {"total_pages": 2},
        ),
        2: (
            [
                {
                    "id": 300,
                    "name": "",
                }
            ],
            {"total_pages": 2},
        ),
    }

    async def fake_list_customers(*, page: int, per_page: int, rate_limiter):
        return pages.get(page, ([], {"total_pages": 2}))

    class DummyLimiter:
        async def acquire(self):
            return None

    async def fake_get_rate_limiter():
        return DummyLimiter()

    monkeypatch.setattr(company_importer.company_repo, "get_company_by_syncro_id", fake_get_company_by_syncro_id)
    monkeypatch.setattr(company_importer.company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(company_importer.company_repo, "create_company", fake_create_company)
    monkeypatch.setattr(company_importer.company_repo, "update_company", fake_update_company)
    monkeypatch.setattr(company_importer.syncro, "list_customers", fake_list_customers)
    monkeypatch.setattr(company_importer.syncro, "get_rate_limiter", fake_get_rate_limiter)

    summary = await company_importer.import_all_companies()

    assert summary.fetched == 3
    assert summary.created == 1
    assert summary.updated == 1
    assert summary.skipped == 1

    created = next((r for r in records if r.get("syncro_company_id") == "200"), None)
    assert created is not None
    assert created["name"] == "Beta Industries"

    updated = next((r for r in records if r.get("syncro_company_id") == "100"), None)
    assert updated is not None
    assert updated["name"] == "Acme Corporation"
    assert "10 Downing" in str(updated.get("address"))

