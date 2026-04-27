from __future__ import annotations

import pytest

from app.repositories import staff


class _DummyStaffDB:
    def __init__(self, rows):
        self.rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    async def fetch_all(self, sql, params):
        self.last_sql = sql.strip()
        self.last_params = params
        return self.rows


class _DummyCustomFieldsRepo:
    async def get_all_staff_field_values(self, company_id, staff_ids):
        return {int(staff_id): {} for staff_id in staff_ids}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_enabled_staff_users_returns_rows(monkeypatch):
    dummy_rows = [
        {"id": 11, "email": "bravo@example.com"},
        {"id": 5, "email": "alpha@example.com"},
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)

    result = await staff.list_enabled_staff_users(3)

    assert dummy_db.last_params == (3,)
    assert "FROM staff AS s" in (dummy_db.last_sql or "")
    assert result == dummy_rows
    assert result is not dummy_rows


@pytest.mark.anyio
async def test_list_enabled_staff_users_handles_empty(monkeypatch):
    dummy_db = _DummyStaffDB([])
    monkeypatch.setattr(staff, "db", dummy_db)

    result = await staff.list_enabled_staff_users(9)

    assert result == []
    assert dummy_db.last_params == (9,)


@pytest.mark.anyio
async def test_list_staff_supports_polling_filters_and_cursor(monkeypatch):
    dummy_rows = [
        {
            "id": 7,
            "company_id": 3,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": "requested",
        }
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)
    monkeypatch.setattr(staff, "staff_custom_fields_repo", _DummyCustomFieldsRepo())

    result = await staff.list_staff(
        3,
        enabled=True,
        onboarding_complete=False,
        onboarding_status="requested",
        offboarding_complete=False,
        offboarding_status="approved",
        created_after="2026-01-01T00:00:00",
        updated_after="2026-01-02T00:00:00",
        offboarding_requested_after="2026-01-04T00:00:00",
        offboarding_updated_after="2026-01-05T00:00:00",
        scheduled_from="2026-01-06T00:00:00",
        scheduled_to="2026-01-07T00:00:00",
        due_only=True,
        cursor="2026-01-03T00:00:00|15",
        page_size=100,
    )

    assert len(result) == 1
    assert "s.onboarding_complete = %s" in (dummy_db.last_sql or "")
    assert "LOWER(s.onboarding_status) = LOWER(%s)" in (dummy_db.last_sql or "")
    assert "LOWER(s.onboarding_status) LIKE 'offboarding_%'" in (dummy_db.last_sql or "")
    assert "LOWER(s.onboarding_status) <> 'offboarding_completed'" in (dummy_db.last_sql or "")
    assert "s.created_at > %s" in (dummy_db.last_sql or "")
    assert "s.updated_at > %s" in (dummy_db.last_sql or "")
    assert "s.date_offboarded > %s" in (dummy_db.last_sql or "")
    assert "e.scheduled_for_utc >= %s" in (dummy_db.last_sql or "")
    assert "e.scheduled_for_utc <= %s" in (dummy_db.last_sql or "")
    assert "LOWER(COALESCE(e.state, '')) IN ('approved', 'offboarding_approved')" in (dummy_db.last_sql or "")
    assert "(s.updated_at > %s OR (s.updated_at = %s AND s.id > %s))" in (dummy_db.last_sql or "")
    assert "ORDER BY s.updated_at ASC, s.id ASC" in (dummy_db.last_sql or "")
    assert "LIMIT %s" in (dummy_db.last_sql or "")
    assert dummy_db.last_params[-1] == 100


@pytest.mark.anyio
async def test_list_staff_exclude_package_staff_filters_package_emails(monkeypatch):
    """list_staff with exclude_package_staff=True removes staff whose email matches package_UUID@ pattern."""
    dummy_rows = [
        {
            "id": 1,
            "company_id": 3,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
        {
            "id": 2,
            "company_id": 3,
            "first_name": "Unknown",
            "last_name": "",
            "email": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae@tenant.onmicrosoft.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
        {
            "id": 3,
            "company_id": 3,
            "first_name": "Unknown",
            "last_name": "",
            "email": "Package_61fd5e57-4ae4-431b-9f34-16dfeed01fbb@TENANT.COM",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)
    monkeypatch.setattr(staff, "staff_custom_fields_repo", _DummyCustomFieldsRepo())

    result = await staff.list_staff(3, enabled=True, exclude_package_staff=True)

    assert len(result) == 1
    assert result[0]["email"] == "alice@example.com"


@pytest.mark.anyio
async def test_list_staff_without_exclude_package_staff_keeps_all(monkeypatch):
    """list_staff without exclude_package_staff keeps package_ staff in results."""
    dummy_rows = [
        {
            "id": 1,
            "company_id": 3,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
        {
            "id": 2,
            "company_id": 3,
            "first_name": "Unknown",
            "last_name": "",
            "email": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae@tenant.onmicrosoft.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)
    monkeypatch.setattr(staff, "staff_custom_fields_repo", _DummyCustomFieldsRepo())

    result = await staff.list_staff(3, enabled=True, exclude_package_staff=False)

    assert len(result) == 2


@pytest.mark.anyio
async def test_list_staff_exclude_package_staff_keeps_non_uuid_package_prefix(monkeypatch):
    """list_staff keeps staff whose email starts with 'package_' but isn't a UUID pattern."""
    dummy_rows = [
        {
            "id": 1,
            "company_id": 3,
            "first_name": "Bob",
            "last_name": "Jones",
            "email": "package_not-a-uuid@example.com",
            "enabled": 1,
            "is_ex_staff": 0,
            "date_onboarded": None,
            "date_offboarded": None,
            "m365_last_sign_in": None,
            "created_at": None,
            "updated_at": None,
            "onboarding_complete": 0,
            "onboarding_completed_at": None,
            "onboarding_status": None,
        },
    ]
    dummy_db = _DummyStaffDB(dummy_rows)
    monkeypatch.setattr(staff, "db", dummy_db)
    monkeypatch.setattr(staff, "staff_custom_fields_repo", _DummyCustomFieldsRepo())

    result = await staff.list_staff(3, enabled=True, exclude_package_staff=True)

    assert len(result) == 1
    assert result[0]["email"] == "package_not-a-uuid@example.com"
