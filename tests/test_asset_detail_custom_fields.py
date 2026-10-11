"""Tests for editing asset custom fields from the asset detail page.

Custom-field editing was moved off the asset list "Edit" modal onto the
``/assets/{id}`` detail page. These tests lock in the move:

* the detail page renders editable custom fields for *every* provenance
  (not only manually created assets);
* saving the detail page form persists custom fields for synced assets;
* saving the detail page form can fire a tray push notification, mirroring
  the behaviour the list modal previously provided;
* the asset list no longer carries the edit action or its modal markup.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.features.assets import routes

ROOT = Path(__file__).resolve().parent.parent
DETAIL_TEMPLATE = ROOT / "app" / "templates" / "assets" / "detail.html"
INDEX_TEMPLATE = ROOT / "app" / "templates" / "assets" / "index.html"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --------------------------------------------------------------------------- #
# Template checks
# --------------------------------------------------------------------------- #

def _custom_fields_section(template: str) -> str:
    start = template.index('id="asset-custom-fields"')
    end = template.index("</section>", start)
    return template[start:end]


def test_detail_page_custom_fields_are_editable_for_every_provenance():
    template = DETAIL_TEMPLATE.read_text()
    section = _custom_fields_section(template)
    # Editable inputs must no longer be gated on manual provenance.
    assert "asset.provenance == 'manual'" not in section
    assert "{% if can_edit %}" in section
    # The editable inputs remain present and bound to the settings form.
    assert 'name="custom_{{ field.id }}"' in section
    assert 'form="asset-settings-form"' in section


def test_detail_page_offers_a_tray_notification_toggle():
    template = DETAIL_TEMPLATE.read_text()
    assert 'name="send_tray_notification"' in template


def test_asset_list_no_longer_offers_custom_field_editing():
    template = INDEX_TEMPLATE.read_text()
    assert "data-edit-asset" not in template
    assert 'id="asset-fields-modal"' not in template
    assert "asset-send-tray-notification" not in template


# --------------------------------------------------------------------------- #
# Route checks
# --------------------------------------------------------------------------- #

def _form_request(body: bytes) -> Request:
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


def _patch_documentation(monkeypatch, *, provenance: str):
    record = {"id": 1, "company_id": 4, "provenance": provenance, "name": "Office Printer"}
    user = {"id": 8, "is_super_admin": True, "company_id": 4}
    monkeypatch.setattr(
        routes,
        "_load_asset_context",
        AsyncMock(return_value=(user, {}, {"id": 4}, 4, None)),
    )
    monkeypatch.setattr(routes.asset_repo, "get_asset_by_id", AsyncMock(return_value=record))
    set_value = AsyncMock()
    monkeypatch.setattr(routes.asset_custom_fields_repo, "set_asset_field_value", set_value)
    monkeypatch.setattr(
        routes.asset_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(return_value=[{"id": 11, "name": "Location", "field_type": "text"}]),
    )
    monkeypatch.setattr(routes.asset_repo, "update_operational_documentation", AsyncMock())
    monkeypatch.setattr(routes.asset_repo, "update_manual_inventory", AsyncMock())
    notify = AsyncMock(return_value={"targeted": 1, "delivered": 1, "queued": 0})
    monkeypatch.setattr(routes.tray_service, "push_notification_to_company_devices", notify)
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(
        routes._main(),
        "flash_redirect",
        lambda *args, **kwargs: Response(status_code=303),
    )
    return set_value, notify


@pytest.mark.anyio
async def test_detail_form_saves_custom_fields_for_synced_asset(monkeypatch):
    set_value, _notify = _patch_documentation(monkeypatch, provenance="syncro")

    response = await routes.update_asset_documentation(
        _form_request(b"owner=Sam&custom_11=Floor2"), asset_id=1
    )

    assert response.status_code == 303
    # Custom fields persist even though the asset is not manually created.
    set_value.assert_awaited_once_with(1, 11, value_text="Floor2")
    routes.asset_repo.update_manual_inventory.assert_not_awaited()


@pytest.mark.anyio
async def test_detail_form_saves_custom_fields_for_manual_asset(monkeypatch):
    set_value, _notify = _patch_documentation(monkeypatch, provenance="manual")
    monkeypatch.setattr(
        routes,
        "_manual_asset_values",
        AsyncMock(
            return_value={
                "name": "Office Printer",
                "type": "Printer / MFP",
                "status": "Active",
                "serial_number": "P-1",
                "location": "Office",
            }
        ),
    )

    await routes.update_asset_documentation(_form_request(b"custom_11=Floor2"), asset_id=1)

    set_value.assert_awaited_once_with(1, 11, value_text="Floor2")


@pytest.mark.anyio
async def test_detail_form_fires_tray_notification_when_opted_in(monkeypatch):
    _set_value, notify = _patch_documentation(monkeypatch, provenance="syncro")

    await routes.update_asset_documentation(
        _form_request(b"owner=Sam&send_tray_notification=1"), asset_id=1
    )

    notify.assert_awaited_once_with(
        company_id=4,
        title="Asset updated",
        body="Office Printer has been updated.",
        asset_ids=[1],
    )


@pytest.mark.anyio
async def test_detail_form_does_not_fire_tray_notification_by_default(monkeypatch):
    _set_value, notify = _patch_documentation(monkeypatch, provenance="syncro")

    await routes.update_asset_documentation(_form_request(b"owner=Sam"), asset_id=1)

    notify.assert_not_awaited()
