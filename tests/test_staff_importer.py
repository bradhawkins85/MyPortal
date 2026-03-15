from __future__ import annotations

import pytest

from app.services import staff_importer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_find_existing_staff_matches_email():
    existing = [
        {"first_name": "John", "last_name": "Doe", "email": "john@example.com"},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="John",
        last_name="Doe",
        email="john@example.com",
    )
    assert result == existing[0]


def test_find_existing_staff_treats_different_email_as_new():
    existing = [
        {"first_name": "John", "last_name": "Doe", "email": "john@example.com"},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="John",
        last_name="Doe",
        email="john2@example.com",
    )
    assert result is None


def test_find_existing_staff_matches_by_name_when_email_missing():
    existing = [
        {"first_name": "Jane", "last_name": "Smith", "email": None},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="Jane",
        last_name="Smith",
        email=None,
    )
    assert result == existing[0]


@pytest.mark.anyio
async def test_import_contacts_raises_when_no_syncro_and_no_m365(monkeypatch):
    """If the company has neither a Syncro mapping nor M365 credentials, raise an error."""
    from app.services import syncro as syncro_service

    monkeypatch.setattr(
        "app.repositories.companies.get_company_by_id",
        lambda *_, **__: _async_return({"id": 1, "syncro_company_id": None}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return(None),
    )

    with pytest.raises(syncro_service.SyncroConfigurationError):
        await staff_importer.import_contacts_for_company(1)


@pytest.mark.anyio
async def test_import_contacts_uses_syncro_when_configured(monkeypatch):
    """When a Syncro mapping is present, staff is imported from Syncro."""
    monkeypatch.setattr(
        "app.repositories.companies.get_company_by_id",
        lambda *_, **__: _async_return({"id": 1, "syncro_company_id": "syncro-123"}),
    )
    monkeypatch.setattr(
        "app.services.syncro.get_contacts",
        lambda *_, **__: _async_return(
            [
                {
                    "id": 42,
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.repositories.staff.list_staff",
        lambda *_, **__: _async_return([]),
    )
    created_calls: list[dict] = []

    async def fake_create_staff(**kwargs):
        created_calls.append(kwargs)
        return {**kwargs, "id": 100}

    monkeypatch.setattr("app.repositories.staff.create_staff", fake_create_staff)

    summary = await staff_importer.import_contacts_for_company(1)
    assert summary.created == 1
    assert summary.updated == 0
    assert len(created_calls) == 1
    assert created_calls[0]["email"] == "alice@example.com"
    assert created_calls[0]["syncro_contact_id"] == "42"


@pytest.mark.anyio
async def test_import_contacts_falls_back_to_m365_when_no_syncro(monkeypatch):
    """When Syncro mapping is absent but M365 credentials exist, staff is imported from M365."""
    monkeypatch.setattr(
        "app.repositories.companies.get_company_by_id",
        lambda *_, **__: _async_return({"id": 2, "syncro_company_id": None}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return({"client_id": "abc", "tenant_id": "xyz"}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_all_users",
        lambda *_, **__: _async_return(
            [
                {
                    "givenName": "Bob",
                    "surname": "Jones",
                    "mail": "bob@example.com",
                    "mobilePhone": "+61400000000",
                    "businessPhones": [],
                    "streetAddress": "1 Main St",
                    "city": "Sydney",
                    "state": "NSW",
                    "postalCode": "2000",
                    "country": "Australia",
                    "department": "Engineering",
                    "jobTitle": "Developer",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.repositories.staff.list_staff",
        lambda *_, **__: _async_return([]),
    )
    created_calls: list[dict] = []

    async def fake_create_staff(**kwargs):
        created_calls.append(kwargs)
        return {**kwargs, "id": 200}

    monkeypatch.setattr("app.repositories.staff.create_staff", fake_create_staff)

    summary = await staff_importer.import_contacts_for_company(2)
    assert summary.created == 1
    assert summary.updated == 0
    assert len(created_calls) == 1
    assert created_calls[0]["email"] == "bob@example.com"
    assert created_calls[0]["department"] == "Engineering"
    assert created_calls[0]["job_title"] == "Developer"
    assert created_calls[0]["syncro_contact_id"] is None


@pytest.mark.anyio
async def test_import_m365_skips_users_without_name(monkeypatch):
    """M365 users with no name or displayName are skipped."""
    monkeypatch.setattr(
        "app.repositories.companies.get_company_by_id",
        lambda *_, **__: _async_return({"id": 3, "syncro_company_id": None}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return({"client_id": "abc", "tenant_id": "xyz"}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_all_users",
        lambda *_, **__: _async_return(
            [
                # No name at all – should be skipped
                {"givenName": None, "surname": None, "displayName": None, "mail": "noname@example.com"},
                # Valid user
                {"givenName": "Carol", "surname": "White", "displayName": "Carol White", "mail": "carol@example.com"},
            ]
        ),
    )
    monkeypatch.setattr(
        "app.repositories.staff.list_staff",
        lambda *_, **__: _async_return([]),
    )
    created_calls: list[dict] = []

    async def fake_create_staff(**kwargs):
        created_calls.append(kwargs)
        return {**kwargs, "id": 300}

    monkeypatch.setattr("app.repositories.staff.create_staff", fake_create_staff)

    summary = await staff_importer.import_contacts_for_company(3)
    assert summary.skipped == 1
    assert summary.created == 1


@pytest.mark.anyio
async def test_import_m365_updates_existing_staff(monkeypatch):
    """M365 import updates an existing staff record matched by email."""
    monkeypatch.setattr(
        "app.repositories.companies.get_company_by_id",
        lambda *_, **__: _async_return({"id": 4, "syncro_company_id": None}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return({"client_id": "abc", "tenant_id": "xyz"}),
    )
    monkeypatch.setattr(
        "app.services.m365.get_all_users",
        lambda *_, **__: _async_return(
            [{"givenName": "Dave", "surname": "Brown", "mail": "dave@example.com"}]
        ),
    )
    existing = [{"id": 99, "first_name": "Dave", "last_name": "Brown", "email": "dave@example.com"}]
    monkeypatch.setattr(
        "app.repositories.staff.list_staff",
        lambda *_, **__: _async_return(existing),
    )
    updated_calls: list[dict] = []

    async def fake_update_staff(staff_id, **kwargs):
        updated_calls.append({"id": staff_id, **kwargs})

    monkeypatch.setattr("app.repositories.staff.update_staff", fake_update_staff)

    summary = await staff_importer.import_contacts_for_company(4)
    assert summary.updated == 1
    assert summary.created == 0
    assert updated_calls[0]["id"] == 99



@pytest.mark.anyio
async def test_import_m365_contacts_bypasses_syncro_when_syncro_id_set(monkeypatch):
    """import_m365_contacts_for_company should use M365 even when a Syncro ID is configured."""
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return({"client_id": "abc", "tenant_id": "xyz"}),
    )
    m365_users_called = []

    async def fake_get_all_users(company_id):
        m365_users_called.append(company_id)
        return [{"givenName": "Eve", "surname": "Green", "mail": "eve@example.com"}]

    monkeypatch.setattr("app.services.m365.get_all_users", fake_get_all_users)
    monkeypatch.setattr(
        "app.repositories.staff.list_staff",
        lambda *_, **__: _async_return([]),
    )

    async def fake_create_staff(**kwargs):
        return {**kwargs, "id": 500}

    monkeypatch.setattr("app.repositories.staff.create_staff", fake_create_staff)

    # Syncro should never be called
    async def syncro_must_not_be_called(*_, **__):
        raise AssertionError("Syncro should not be called")

    monkeypatch.setattr("app.services.syncro.get_contacts", syncro_must_not_be_called)

    summary = await staff_importer.import_m365_contacts_for_company(5)
    assert summary.created == 1
    assert m365_users_called == [5]


@pytest.mark.anyio
async def test_import_m365_contacts_returns_empty_when_no_credentials(monkeypatch):
    """import_m365_contacts_for_company returns an empty summary when M365 is not configured."""
    monkeypatch.setattr(
        "app.services.m365.get_credentials",
        lambda *_, **__: _async_return(None),
    )

    summary = await staff_importer.import_m365_contacts_for_company(6)
    assert summary.created == 0
    assert summary.updated == 0
    assert summary.skipped == 0
    assert summary.total == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _async_return(value):
    return value

