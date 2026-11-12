"""Read Only and Read Write asset access.

Read Only staff see the same asset page as Read Write, without editing it.
Read Write may change only the asset profile (and photos, through the asset
photos permission). Asset type overrides, custom fields, data provenance,
warranty and reconciliation changes, customer visibility and archiving are
reserved for super admins.
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


def _install(monkeypatch, membership):
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
    monkeypatch.setattr(
        routes, "_asset_write_context",
        AsyncMock(side_effect=HTTPException(status_code=403))
        if membership is READ_ONLY
        else AsyncMock(return_value=(dict(STAFF_USER), {"id": 4}, 4, None)),
    )
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
    monkeypatch.setattr(
        routes, "_asset_write_context",
        AsyncMock(return_value=(dict(STAFF_USER), {"id": 4}, 4, None)),
    )
    lookup = AsyncMock()
    monkeypatch.setattr(warranty_lookup, "lookup_asset", lookup)

    with pytest.raises(HTTPException) as excinfo:
        await routes.lookup_asset_warranty(_form_request(), 1)

    assert excinfo.value.status_code == 403
    lookup.assert_not_awaited()


def _section(template: str, section_id: str) -> str:
    """The template text from the guard before a section to its end."""
    marker = template.index(f'id="{section_id}"')
    start = template.rindex("{% if", 0, marker)
    return template[start:template.index("</section>", marker)]


@pytest.mark.parametrize(
    "section_id", ["asset-custom-fields", "source-records", "asset-visibility"]
)
def test_reserved_sections_render_for_super_admins_only(section_id):
    section = _section(DETAIL_TEMPLATE.read_text(), section_id)
    assert section.startswith("{% if can_manage_asset %}")


def test_archive_section_shares_the_super_admin_guard():
    template = DETAIL_TEMPLATE.read_text()
    guard = template.rindex("{% if", 0, template.index('id="asset-visibility"'))
    archive = template.index('id="asset-archive"')
    assert "{% endif %}" not in template[guard:archive].split("</section>", 1)[1]


def test_reserved_nav_entries_are_for_super_admins_only():
    template = DETAIL_TEMPLATE.read_text()
    for label in ("Custom fields", "Data provenance", "Customer visibility", "Archive & restore"):
        line = next(line for line in template.splitlines()
                    if f'"{label}"' in line and line.lstrip().startswith("("))
        assert line.rstrip().endswith("can_manage_asset),"), label


def test_inventory_asset_type_override_is_for_super_admins_only():
    template = DETAIL_TEMPLATE.read_text()
    form = template.index('action="/assets/{{ asset.id }}/asset-type"')
    guard = template.rindex("{% if", 0, form)
    assert template[guard:form].startswith("{% if can_manage_asset and asset.provenance != 'manual' %}")


def test_profile_inputs_are_read_only_without_write_access():
    section = _section(DETAIL_TEMPLATE.read_text(), "asset-profile")
    assert section.startswith("{% if not customer_safe %}")
    assert "{% if not can_edit %}disabled{% endif %}" in section
