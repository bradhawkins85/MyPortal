from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import main
from app.api.routes import click_to_call, users
from app.schemas.users import UserUpdate
from app.security import profile_access


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "user,membership,allowed",
    [
        ({"is_super_admin": True}, None, True),
        ({}, None, False),
        ({}, {"menu_permissions": {"menu.tickets": "write"}}, False),
        ({}, {"menu_permissions": {"menu.reporting": "write"}}, False),
        ({}, {"menu_permissions": {"menu.admin.profile": "write"}}, False),
        ({}, {"is_admin": True}, True),
        ({}, {"menu_permissions": {"menu.admin.company": "write"}}, True),
        ({}, {"menu_permissions": {"menu.admin.company": "read"}}, False),
        ({}, {"menu_permissions": {"menu.admin.technician": "write"}}, True),
        ({}, {"permissions": ["company.switch_all"]}, True),
        ({}, {"permissions": ["company.admin"]}, True),
    ],
)
def test_profile_sections_require_admin_or_technician(user, membership, allowed):
    assert main._can_edit_profile_technician_tools(user, membership) is allowed


def _request(membership):
    return SimpleNamespace(state=SimpleNamespace(active_membership=membership))


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["connect", "disconnect", "phones", "call_get", "call_update"])
async def test_integration_endpoints_deny_ticket_only_roles(monkeypatch, action):
    request = _request({"menu_permissions": {"menu.tickets": "write"}})
    user = {"id": 7}
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    lookup = AsyncMock()
    delete = AsyncMock()
    get_settings = AsyncMock()
    from app.repositories import user_m365_contacts

    monkeypatch.setattr(main.user_m365_contacts_service, "lookup_phones", lookup)
    monkeypatch.setattr(user_m365_contacts, "delete_integration", delete)
    monkeypatch.setattr(click_to_call.click_to_call_repo, "get_settings", get_settings)
    with pytest.raises(HTTPException) as exc:
        if action == "connect":
            await main.profile_m365_contacts_connect(request)
        elif action == "disconnect":
            await main.profile_m365_contacts_disconnect(request)
        elif action == "phones":
            await main.profile_m365_contact_phones(request, name="Ada")
        elif action == "call_get":
            await click_to_call.get_settings(request=request, current_user=user)
        else:
            await click_to_call.update_settings(
                click_to_call.ClickToCallSettingsUpdate(), request=request, current_user=user
            )
    assert exc.value.status_code == 403
    lookup.assert_not_awaited()
    delete.assert_not_awaited()
    get_settings.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["mobile_phone", "booking_link_url", "email_signature", "matrix_user_id"])
async def test_work_details_cannot_be_updated_by_ticket_only_roles(monkeypatch, field):
    monkeypatch.setattr(users.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 7}))
    update = AsyncMock()
    monkeypatch.setattr(users.user_repo, "update_user", update)
    with pytest.raises(HTTPException) as exc:
        await users.update_user(
            7, UserUpdate(**{field: None}),
            request=_request({"menu_permissions": {"menu.tickets": "write"}}),
            current_user={"id": 7},
        )
    assert exc.value.status_code == 403
    update.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["menu.admin.company", "menu.admin.technician"])
async def test_authorized_role_can_update_work_details(monkeypatch, key):
    monkeypatch.setattr(users.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 7}))
    update = AsyncMock(return_value={"id": 7, "booking_link_url": "https://example.com/book"})
    monkeypatch.setattr(users.user_repo, "update_user", update)
    monkeypatch.setattr(users.audit_service, "record", AsyncMock())
    await users.update_user(
        7, UserUpdate(booking_link_url="https://example.com/book"),
        request=_request({"menu_permissions": {key: "write"}}), current_user={"id": 7},
    )
    update.assert_awaited_once_with(7, booking_link_url="https://example.com/book")


@pytest.mark.anyio
async def test_api_access_uses_active_session_company(monkeypatch):
    request = _request(None)
    load = AsyncMock(return_value=SimpleNamespace(active_company_id=9))
    lookup = AsyncMock(return_value={"menu_permissions": {"menu.tickets": "write"}})
    monkeypatch.setattr(profile_access.session_manager, "load_session", load)
    monkeypatch.setattr(profile_access.user_company_repo, "get_user_company", lookup)
    with pytest.raises(HTTPException) as exc:
        await profile_access.require_profile_technician_tools(request, {"id": 7, "company_id": 1})
    assert exc.value.status_code == 403
    lookup.assert_awaited_once_with(7, 9)


@pytest.mark.anyio
async def test_simulated_role_does_not_fall_back_to_real_membership(monkeypatch):
    request = _request({"menu_permissions": {"menu.tickets": "write"}})
    lookup = AsyncMock()
    monkeypatch.setattr(profile_access.user_company_repo, "get_user_company", lookup)
    with pytest.raises(HTTPException):
        await profile_access.require_profile_technician_tools(request, {"id": 1, "is_super_admin": False})
    lookup.assert_not_awaited()
