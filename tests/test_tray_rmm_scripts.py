"""Tray menu items that run a MyPortal RMM script on the tray device's computer."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import tray as tray_routes
from app.schemas.tray import TrayConfigResponse, TrayRMMScriptRunRequest
from app.services import rmm_scripts

MENU = [
    {"type": "label", "label": "Help"},
    {
        "type": "submenu",
        "label": "Fixes",
        "children": [
            {"type": "rmm_script", "label": "Clear Chrome cache", "script_id": 4, "node_id": "chrome",
             "entries": {"param:Browser": "chrome"}},
            {"type": "rmm_script", "label": "Clear Edge cache", "script_id": 4, "node_id": "edge", "confirm": True,
             "entries": {"param:Browser": "edge", "env:API_TOKEN": "s3cret"}},
            {"type": "TRMM_Script", "label": "TRMM", "script_id": 9},
        ],
    },
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_menu_item_is_found_by_script_and_node_id():
    find = tray_routes._find_rmm_script_node
    assert find(MENU, 4, "edge")["label"] == "Clear Edge cache"
    assert find(MENU, 4, None)["label"] == "Clear Chrome cache"
    assert find(MENU, 4, "other") is None
    # A Tactical RMM item with the same id is not a MyPortal RMM item.
    assert find(MENU, 9, None) is None


def test_script_values_are_never_sent_to_the_tray():
    menu = TrayConfigResponse(version=1, menu=MENU).model_dump()["menu"]
    edge = menu[1]["children"][1]
    assert edge["confirm"] is True and edge["node_id"] == "edge" and edge["script_id"] == 4
    assert "entries" not in edge
    assert "s3cret" not in TrayConfigResponse(version=1, menu=MENU).model_dump_json()


@pytest.mark.anyio
async def test_endpoint_queues_only_items_in_the_devices_menu(monkeypatch):
    queued = []

    async def resolve(device):
        return {"menu": MENU}

    async def queue_tray_run(*, device, node):
        queued.append(node["node_id"])
        if node["node_id"] == "chrome":
            raise rmm_scripts.RunRequestError({"busy": "This is already queued or running on this computer."})
        return {"run_id": 77, "script": {"id": 4, "name": "Clear-Cache"}}

    monkeypatch.setattr(tray_routes.tray_service, "resolve_config_for_device", resolve)
    monkeypatch.setattr(tray_routes.rmm_scripts, "queue_tray_run", queue_tray_run)
    device = {"id": 1, "company_id": 1, "asset_id": 10}

    response = await tray_routes.run_rmm_script(TrayRMMScriptRunRequest(script_id=4, node_id="edge"), device=device)
    assert response.run_id == 77 and response.script_name == "Clear Edge cache"
    assert response.message == "Clear Edge cache will run on this computer shortly."

    with pytest.raises(HTTPException) as busy:
        await tray_routes.run_rmm_script(TrayRMMScriptRunRequest(script_id=4, node_id="chrome"), device=device)
    assert busy.value.status_code == 409 and "already" in busy.value.detail

    with pytest.raises(HTTPException) as refused:
        await tray_routes.run_rmm_script(TrayRMMScriptRunRequest(script_id=5), device=device)
    assert refused.value.status_code == 403
    assert queued == ["edge", "chrome"]


def test_tray_runs_are_labelled_in_run_history():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "run.run_source == 'tray'" in (root / "app/templates/rmm/_macros.html").read_text()
    assert "run.run_source === 'tray'" in (root / "app/static/js/rmm_scripts.js").read_text()
