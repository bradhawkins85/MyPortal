"""Each Assets list row's Actions menu mirrors the asset page's header menu."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1] / "app/templates"
IMPORTS = (
    '{% import "rmm/_macros.html" as rmm_ui %}'
    '{% from "macros/header.html" import page_header_overflow with context %}'
    '{% import "assets/_action_icons.html" as action_icons %}'
    '{% from "tags/_picker.html" import tag_list %}'
)


@pytest.fixture
def page():
    source = (ROOT / "assets/index.html").read_text()
    content = source.split("{% block content %}", 1)[1].split("{% endblock %}", 1)[0]
    env = Environment(loader=FileSystemLoader(ROOT), autoescape=True)
    env.globals["static_url"] = lambda path: path
    return env.from_string(IMPORTS + content)


def _render(page, *assets, **extra):
    values = {
        "assets": list(assets),
        "has_assets": True,
        "columns": [],
        "stats": {},
        "is_super_admin": True,
        "can_write_assets": True,
        "rmm_actions": any(asset.get("rmm_actions") for asset in assets),
        "rmm_remote_providers": [{"provider": "rustdesk", "label": "RustDesk"}],
        "rmm_command_languages": [{"value": "powershell", "label": "PowerShell"}, {"value": "cmd", "label": "Command Prompt"}],
        "csrf_token": "token",
        **extra,
    }
    return page.render(**values)


def _menu(html, asset_id):
    return html.split(f'id="asset-actions-{asset_id}"', 1)[1].split("</td>", 1)[0]


AGENT = {
    "id": 10, "company_id": 1, "name": "PC", "rmm_actions": True, "can_push_rmm_agent": True,
    "rmm_windows": True, "rmm_command_languages": "powershell,cmd", "rmm_tray_repair": True, "rmm_uninstall": True,
    "can_health_check": True, "health_check_ready": True,
}


def test_row_menu_groups_the_asset_page_options_with_icons(page):
    menu = _menu(_render(page, AGENT), 10)
    headings = [part.split("<", 1)[0] for part in menu.split('class="header-menu__heading" role="presentation">')[1:]]
    assert headings == ["Asset", "Remote access", "RMM", "Agent maintenance", "Go to"]
    labels = re.findall(r"<span>([^<]+)</span>", menu)
    for label in ("Open asset", "Start process", "Customer preview", "RustDesk remote desktop", "RustDesk remote desktop (Prompt)", "Check in now",
                  "Run health check", "Run script", "Execute command", "Install Choco package",
                  "Repair RMM agent", "Repair tray agent", "Company credentials", "Uninstall agents", "Delete"):
        assert label in labels
    assert "Push RMM agent" not in labels
    assert menu.count('class="header-menu__icon"') == menu.count('class="header-menu__item')
    assert 'action="/rmm/assets/10/checkin"' in menu and 'action="/rmm/assets/10/health-check"' in menu
    assert 'data-rmm-command-asset="10"' in menu and 'data-rmm-command-languages="powershell,cmd"' in menu
    assert 'data-choco-modal-asset="10"' in menu and 'data-rmm-run-asset="10"' in menu


def test_row_without_an_agent_offers_the_push(page):
    html = _render(page, {"id": 11, "company_id": 1, "name": "Mac", "can_push_rmm_agent": True})
    labels = re.findall(r"<span>([^<]+)</span>", _menu(html, 11))
    assert "Push RMM agent" in labels and "Repair RMM agent" in labels
    assert "Run script" not in labels and "Check in now" not in labels
    # Uninstalling goes through the RMM agent; deleting does not.
    assert "Uninstall agents" not in labels and "Delete" in labels


def test_customer_rows_keep_only_the_asset_options(page):
    html = _render(page, {"id": 12, "company_id": 1, "name": "Laptop"}, is_super_admin=False, can_write_assets=False)
    labels = re.findall(r"<span>([^<]+)</span>", _menu(html, 12))
    assert labels == ["Open asset", "Start process"]
    assert "header-menu__divider" not in _menu(html, 12)


def test_meshcentral_functions_stay_in_row_actions_without_a_connect_shortcut(page):
    providers = [{"provider": "meshcentral", "label": "MeshCentral"}]
    html = _render(page, AGENT, {"id": 11, "name": "No agent"}, rmm_remote_providers=providers)
    assert "Connect with MeshCentral" not in html
    cell = html.split('<td class="table__actions"', 1)[1].split("</td>", 1)[0]
    assert 'data-rmm-remote="meshcentral"' not in cell.split('data-header-menu', 1)[0]
    menu = _menu(html, 10)
    assert menu.count('data-rmm-remote="meshcentral"') == 3
    assert menu.count('data-rmm-remote-asset="10"') == 3
    for mode, label in (("remote", "remote desktop"), ("file_transfer", "file transfer"), ("terminal", "terminal")):
        assert f'data-rmm-remote-mode="{mode}"' in menu
        assert f"MeshCentral {label}" in menu
    assert 'data-rmm-remote="meshcentral"' not in _menu(html, 11)


@pytest.mark.parametrize("can_wake,can_write,visible", [(True, True, True), (False, True, False), (True, False, False)])
def test_wake_available_without_an_agent_only_to_authorised_staff(page, can_wake, can_write, visible):
    html = _render(page, {"id": 11, "company_id": 1, "name": "Offline PC"},
                   can_wake=can_wake, can_write_assets=can_write)
    menu = _menu(html, 11)
    assert ('data-wol-asset-id="11"' in menu) is visible
    assert ('id="wol-status-11"' in menu) is visible
    if visible:
        assert "Wake on LAN" in menu and "data-confirm=" in menu
