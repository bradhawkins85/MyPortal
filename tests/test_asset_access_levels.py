"""Read Only and Read Write asset access.

For roles that are neither admins nor technicians, Read Only sees the same
asset page as Read Write, without editing it, and Read Write may change only
the asset profile (and photos, through the asset photos permission). Custom
fields and data provenance are hidden from them, and asset type overrides,
warranty and reconciliation changes and archiving are left to admins and
technicians. Customer visibility stays with super admins.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.features.assets import routes

ROOT = Path(__file__).resolve().parent.parent
DETAIL_TEMPLATE = ROOT / "app" / "templates" / "assets" / "detail.html"

READ_ONLY = {"menu_permissions": {"menu.assets": "read"}}
READ_WRITE = {"menu_permissions": {"menu.assets": "write"}}
STAFF_USER = {"id": 8, "company_id": 4, "is_super_admin": False}
RECORD = {"id": 1, "company_id": 4, "provenance": "syncro", "name": "Office Printer"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _form_request(body: bytes = b"") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/assets/1",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
        receive,
    )


def _install(monkeypatch, membership, *, technician=False):
    monkeypatch.setattr(routes._main(), "_is_helpdesk_technician", AsyncMock(return_value=technician))
    monkeypatch.setattr(
        routes, "_load_asset_context",
        AsyncMock(return_value=(dict(STAFF_USER), membership, {"id": 4}, 4, None)),
    )
    monkeypatch.setattr(routes.asset_repo, "get_asset_by_id", AsyncMock(return_value=dict(RECORD)))
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(routes._main(), "flash_redirect", lambda *a, **k: Response(status_code=303))


@pytest.mark.anyio
async def test_read_write_saves_the_profile_but_not_custom_fields(monkeypatch):
    _install(monkeypatch, READ_WRITE)
    set_value = AsyncMock()
    monkeypatch.setattr(routes.asset_custom_fields_repo, "set_asset_field_value", set_value)
    monkeypatch.setattr(
        routes.asset_custom_fields_repo, "list_field_definitions",
        AsyncMock(return_value=[{"id": 11, "name": "Location", "field_type": "text"}]),
    )
    update_profile = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "update_operational_documentation", update_profile)

    response = await routes.update_asset_documentation(
        _form_request(b"owner=Sam&custom_11=Floor2"), asset_id=1
    )

    assert response.status_code == 303
    update_profile.assert_awaited_once()
    assert update_profile.await_args.kwargs["owner"] == "Sam"
    set_value.assert_not_awaited()


@pytest.mark.anyio
async def test_read_only_cannot_save_the_profile(monkeypatch):
    _install(monkeypatch, READ_ONLY)
    update_profile = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "update_operational_documentation", update_profile)

    with pytest.raises(HTTPException) as excinfo:
        await routes.update_asset_documentation(_form_request(b"owner=Sam"), asset_id=1)

    assert excinfo.value.status_code == 403
    update_profile.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("membership", [READ_ONLY, READ_WRITE])
@pytest.mark.parametrize(
    ("handler", "args", "repo_method"),
    [
        (routes.archive_asset, (1,), "set_asset_archived"),
        (routes.update_asset_type, (1,), "set_asset_type"),
        (routes.save_manual_asset_warranty, (1,), "set_manual_warranty"),
        (routes.approve_asset_reconciliation, (1, 5), "approve_reconciliation"),
        (routes.publish_asset, (1,), "set_customer_visible"),
    ],
)
async def test_non_super_admins_cannot_change_reserved_settings(
    monkeypatch, membership, handler, args, repo_method
):
    _install(monkeypatch, membership)
    change = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, repo_method, change)

    with pytest.raises(HTTPException) as excinfo:
        await handler(_form_request(b"action=restore"), *args)

    assert excinfo.value.status_code == 403
    change.assert_not_awaited()


@pytest.mark.anyio
async def test_read_write_cannot_look_up_the_warranty(monkeypatch):
    from app.services import warranty_lookup

    _install(monkeypatch, READ_WRITE)
    lookup = AsyncMock()
    monkeypatch.setattr(warranty_lookup, "lookup_asset", lookup)

    with pytest.raises(HTTPException) as excinfo:
        await routes.lookup_asset_warranty(_form_request(), 1)

    assert excinfo.value.status_code == 403
    lookup.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("membership", "technician"),
    [
        (READ_WRITE, True),
        ({"menu_permissions": {"menu.assets": "write", "menu.admin.technician": "write"}}, False),
        ({"menu_permissions": {"menu.assets": "write", "menu.admin.company": "write"}}, False),
    ],
)
async def test_admins_and_technicians_keep_full_asset_management(monkeypatch, membership, technician):
    _install(monkeypatch, membership, technician=technician)
    set_type = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_asset_type", set_type)
    archive = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_asset_archived", archive)
    set_value = AsyncMock()
    monkeypatch.setattr(routes.asset_custom_fields_repo, "set_asset_field_value", set_value)
    monkeypatch.setattr(
        routes.asset_custom_fields_repo, "list_field_definitions",
        AsyncMock(return_value=[{"id": 11, "name": "Location", "field_type": "text"}]),
    )
    monkeypatch.setattr(routes.asset_repo, "update_operational_documentation", AsyncMock())
    monkeypatch.setattr(routes.asset_column_restrictions_repo, "list_restricted_keys", AsyncMock(return_value=set()))
    monkeypatch.setattr(routes._main(), "_get_effective_company_membership", AsyncMock(return_value=membership))

    await routes.update_asset_type(_form_request(b"asset_type=laptop"), 1)
    await routes.archive_asset(_form_request(b"action=archive"), 1)
    await routes.update_asset_documentation(_form_request(b"custom_11=Floor2"), asset_id=1)

    set_type.assert_awaited_once_with(4, 1, "laptop")
    archive.assert_awaited_once_with(1, True)
    set_value.assert_awaited_once_with(1, 11, value_text="Floor2")


@pytest.mark.anyio
async def test_read_only_technicians_cannot_manage_assets(monkeypatch):
    _install(monkeypatch, READ_ONLY, technician=True)
    set_type = AsyncMock()
    monkeypatch.setattr(routes.asset_repo, "set_asset_type", set_type)

    with pytest.raises(HTTPException) as excinfo:
        await routes.update_asset_type(_form_request(b"asset_type=laptop"), 1)

    assert excinfo.value.status_code == 403
    set_type.assert_not_awaited()


def _section(template: str, section_id: str) -> str:
    """The template text from the guard before a section to its end."""
    marker = template.index(f'id="{section_id}"')
    start = template.rindex("{% if", 0, marker)
    return template[start:template.index("</section>", marker)]


@pytest.mark.parametrize(
    ("section_id", "guard"),
    [
        ("asset-custom-fields", "is_asset_staff"),
        ("source-records", "is_asset_staff"),
        ("asset-visibility", "can_manage_visibility"),
        ("asset-archive", "can_manage_asset"),
    ],
)
def test_reserved_sections_are_guarded(section_id, guard):
    section = _section(DETAIL_TEMPLATE.read_text(), section_id)
    assert section.lstrip().startswith("{% if " + guard + " %}")


@pytest.mark.parametrize(
    ("label", "guard"),
    [
        ("Custom fields", "is_asset_staff"),
        ("Data provenance", "is_asset_staff"),
        ("Customer visibility", "can_manage_visibility"),
        ("Archive & restore", "can_manage_asset"),
    ],
)
def test_reserved_nav_entries_are_guarded(label, guard):
    template = DETAIL_TEMPLATE.read_text()
    line = next(line for line in template.splitlines()
                if f'"{label}"' in line and line.lstrip().startswith("("))
    assert line.rstrip().endswith(guard + "),"), label


def test_custom_field_inputs_need_management_access():
    section = _section(DETAIL_TEMPLATE.read_text(), "asset-custom-fields")
    assert "can_edit" not in section
    assert "{% if can_manage_asset %}" in section


def test_inventory_asset_type_override_needs_management_access():
    template = DETAIL_TEMPLATE.read_text()
    form = template.index('action="/assets/{{ asset.id }}/asset-type"')
    guard = template.rindex("{% if", 0, form)
    assert template[guard:form].startswith("{% if can_manage_asset and asset.provenance != 'manual' %}")


def test_profile_inputs_are_read_only_without_write_access():
    section = _section(DETAIL_TEMPLATE.read_text(), "asset-profile")
    assert section.startswith("{% if not customer_safe %}")
    assert "{% if not can_edit %}disabled{% endif %}" in section
