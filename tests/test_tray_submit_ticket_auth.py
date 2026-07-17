from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.anyio
async def test_tray_submit_ticket_uses_bearer_token_without_device_uid(monkeypatch):
    from app.api.routes import tray as tray_routes
    from app.schemas.tray import TrayTicketSubmitRequest

    captured_hashes: list[str] = []
    created: dict[str, object] = {}

    async def fake_get_device_by_auth_hash(token_hash: str):
        captured_hashes.append(token_hash)
        return {
            "id": 123,
            "uid": "device-from-token",
            "status": "active",
            "company_id": 456,
            "asset_id": None,
        }

    async def fake_get_user_by_email(email: str):
        return None

    async def fake_get_questions_for_company(company_id: int | None):
        created["questions_company_id"] = company_id
        return []

    async def fake_resolve_status_or_default(status: str | None):
        return "open"

    async def fake_create_ticket(**kwargs):
        created.update(kwargs)
        return {"id": 789, "ticket_number": "T-789"}

    monkeypatch.setattr(
        tray_routes.tray_repo,
        "get_device_by_auth_hash",
        fake_get_device_by_auth_hash,
    )
    monkeypatch.setattr(
        tray_routes.users_repo, "get_user_by_email", fake_get_user_by_email
    )
    monkeypatch.setattr(
        tray_routes.tq_service,
        "get_questions_for_company",
        fake_get_questions_for_company,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service,
        "resolve_status_or_default",
        fake_resolve_status_or_default,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service, "create_ticket", fake_create_ticket
    )

    request = SimpleNamespace(headers={"Authorization": "Bearer token-abc"})
    payload = TrayTicketSubmitRequest(
        name="Jane",
        email="JANE@EXAMPLE.COM",
        subject="Help",
        description="Broken",
    )

    response = await tray_routes.tray_submit_ticket(payload, request)  # type: ignore[arg-type]

    assert captured_hashes
    assert response.ticket_id == 789
    assert created["company_id"] == 456
    assert created["requester_email"] == "jane@example.com"
    assert created["questions_company_id"] == 456


@pytest.mark.anyio
async def test_tray_submit_ticket_matches_requester_by_phone_when_email_is_unknown(
    monkeypatch,
):
    from app.api.routes import tray as tray_routes
    from app.schemas.tray import TrayTicketSubmitRequest

    created: dict[str, object] = {}
    phone_lookups: list[str] = []

    async def fake_get_device_by_uid(device_uid: str):
        return {
            "id": 123,
            "uid": device_uid,
            "status": "active",
            "company_id": 456,
            "asset_id": None,
        }

    async def fake_get_user_by_email(email: str):
        assert email == "unknown@example.com"
        return None

    async def fake_get_user_by_phone(phone: str):
        phone_lookups.append(phone)
        return {"id": 42}

    async def fake_get_questions_for_company(company_id: int | None):
        return []

    async def fake_resolve_status_or_default(status: str | None):
        return "open"

    async def fake_create_ticket(**kwargs):
        created.update(kwargs)
        return {"id": 789, "ticket_number": "T-789"}

    monkeypatch.setattr(
        tray_routes.tray_repo, "get_device_by_uid", fake_get_device_by_uid
    )
    monkeypatch.setattr(
        tray_routes.users_repo, "get_user_by_email", fake_get_user_by_email
    )
    monkeypatch.setattr(
        tray_routes.users_repo, "get_user_by_phone", fake_get_user_by_phone
    )
    monkeypatch.setattr(
        tray_routes.tq_service,
        "get_questions_for_company",
        fake_get_questions_for_company,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service,
        "resolve_status_or_default",
        fake_resolve_status_or_default,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service, "create_ticket", fake_create_ticket
    )

    payload = TrayTicketSubmitRequest(
        device_uid="device-abc",
        name="Jane",
        email="unknown@example.com",
        phone="+1 (555) 010-1234",
        subject="Help",
    )
    await tray_routes.tray_submit_ticket(payload, SimpleNamespace(headers={}))  # type: ignore[arg-type]

    assert phone_lookups == ["+1 (555) 010-1234"]
    assert created["requester_id"] == 42
    assert created["requester_email"] is None


@pytest.mark.anyio
async def test_tray_submit_ticket_prefers_email_match_over_phone(monkeypatch):
    from app.api.routes import tray as tray_routes
    from app.schemas.tray import TrayTicketSubmitRequest

    created: dict[str, object] = {}

    async def fake_get_device_by_uid(device_uid: str):
        return {
            "id": 123,
            "uid": device_uid,
            "status": "active",
            "company_id": None,
            "asset_id": None,
        }

    async def fake_get_user_by_email(email: str):
        return {"id": 10}

    async def fail_get_user_by_phone(phone: str):
        raise AssertionError("phone lookup must not run after an email match")

    async def fake_get_questions_for_company(company_id: int | None):
        return []

    async def fake_resolve_status_or_default(status: str | None):
        return "open"

    async def fake_create_ticket(**kwargs):
        created.update(kwargs)
        return {"id": 789, "ticket_number": "T-789"}

    monkeypatch.setattr(
        tray_routes.tray_repo, "get_device_by_uid", fake_get_device_by_uid
    )
    monkeypatch.setattr(
        tray_routes.users_repo, "get_user_by_email", fake_get_user_by_email
    )
    monkeypatch.setattr(
        tray_routes.users_repo, "get_user_by_phone", fail_get_user_by_phone
    )
    monkeypatch.setattr(
        tray_routes.tq_service,
        "get_questions_for_company",
        fake_get_questions_for_company,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service,
        "resolve_status_or_default",
        fake_resolve_status_or_default,
    )
    monkeypatch.setattr(
        tray_routes.tickets_service, "create_ticket", fake_create_ticket
    )

    payload = TrayTicketSubmitRequest(
        device_uid="device-abc",
        name="Jane",
        email="jane@example.com",
        phone="555-0100",
        subject="Help",
    )
    await tray_routes.tray_submit_ticket(payload, SimpleNamespace(headers={}))  # type: ignore[arg-type]

    assert created["requester_id"] == 10


@pytest.mark.anyio
async def test_tray_submit_syncro_ticket_keeps_contact_details_when_contact_matches(
    monkeypatch,
):
    from app.api.routes import tray as tray_routes
    from app.schemas.tray import TrayTicketSubmitRequest

    created_payload: dict[str, object] = {}

    async def fake_get_device_by_uid(device_uid: str):
        return {
            "id": 123,
            "uid": device_uid,
            "status": "active",
            "company_id": 456,
            "asset_id": None,
        }

    async def fake_get_questions_for_company(company_id: int | None):
        return []

    async def fake_get_company_by_id(company_id: int):
        return {"id": company_id, "syncro_company_id": "789"}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        return {"id": 321, "syncro_contact_id": "654"}

    async def fake_create_ticket(payload: dict[str, object]):
        created_payload.update(payload)
        return {"id": 987, "number": "S-987"}

    monkeypatch.setattr(
        tray_routes.tray_repo, "get_device_by_uid", fake_get_device_by_uid
    )
    monkeypatch.setattr(
        tray_routes.tq_service,
        "get_questions_for_company",
        fake_get_questions_for_company,
    )
    monkeypatch.setattr(
        tray_routes.companies_repo,
        "get_company_by_id",
        fake_get_company_by_id,
    )
    monkeypatch.setattr(
        tray_routes.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )
    monkeypatch.setattr(tray_routes.syncro_service, "create_ticket", fake_create_ticket)

    request = SimpleNamespace(headers={})
    payload = TrayTicketSubmitRequest(
        device_uid="device-abc",
        name="Jane Contact",
        email="JANE@EXAMPLE.COM",
        phone="555-0100",
        subject="Help",
        description="Broken",
    )

    response = await tray_routes.tray_submit_syncro_ticket(payload, request)  # type: ignore[arg-type]

    assert response.ticket_id == 987
    assert created_payload["customer_id"] == 789
    assert created_payload["contact_id"] == 654
    assert "email" not in created_payload
    comment = created_payload["comments_attributes"][0]["body"]  # type: ignore[index]
    assert "**Name:** Jane Contact" in comment
    assert "**Phone:** 555-0100" in comment
    assert "**Email:** jane@example.com" in comment
    assert "Broken" in comment
