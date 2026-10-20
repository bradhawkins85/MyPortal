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


@pytest.mark.anyio
async def test_menu_hides_scripts_not_enabled_for_the_devices_platform(monkeypatch):
    from app.repositories import rmm as rmm_repo
    from app.services import tray as tray_service

    scripts = {
        1: {"id": 1, "language": "powershell", "platforms": ["windows"]},
        2: {"id": 2, "language": "bash", "platforms": ["darwin", "linux"]},
        3: {"id": 3, "language": "bash", "platforms": ["windows", "darwin"]},
    }

    async def get_script(script_id, *, with_content=False):
        return scripts.get(script_id)

    monkeypatch.setattr(rmm_repo, "get_script", get_script)
    menu = [
        {"type": "rmm_script", "label": "Windows fix", "script_id": 1},
        {"type": "submenu", "label": "Windows only", "children": [
            {"type": "rmm_script", "label": "Windows fix", "script_id": 1},
        ]},
        {"type": "submenu", "label": "Mixed", "children": [
            {"type": "rmm_script", "label": "Mac fix", "script_id": 2},
            {"type": "rmm_script", "label": "Both", "script_id": 3},
        ]},
        {"type": "link", "label": "Portal", "url": "/"},
    ]

    mac = await tray_service._filter_rmm_scripts_for_platform(menu, "macos")
    assert [node["label"] for node in mac] == ["Mixed", "Portal"]
    assert [node["label"] for node in mac[0]["children"]] == ["Mac fix", "Both"]

    windows = await tray_service._filter_rmm_scripts_for_platform(menu, "windows")
    assert [node["label"] for node in windows] == ["Windows fix", "Windows only", "Mixed", "Portal"]
    assert [node["label"] for node in windows[2]["children"]] == ["Both"]

    # A device that has not reported its OS keeps the menu as configured.
    assert await tray_service._filter_rmm_scripts_for_platform(menu, None) == menu
