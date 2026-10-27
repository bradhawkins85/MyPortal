"""The asset page's header Actions menu, which holds the RMM actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1] / "app/templates"


@pytest.fixture
def header():
    source = (ROOT / "assets/detail.html").read_text()
    fragment = source.split("{% block header_actions %}", 1)[1].split("{% endblock %}", 1)[0]
    env = Environment(loader=FileSystemLoader(ROOT))
    return env.from_string(
        '{% from "macros/header.html" import page_header_actions with context %}'
        '{% import "rmm/_macros.html" as rmm_agent_ui %}' + fragment
    )


def _values(**rmm):
    base = {
        "can_run": True,
        "agent": {"os": "windows"},
        "remote_control": [{"provider": "rustdesk", "label": "RustDesk"}],
        "command_languages": [{"value": "powershell", "label": "PowerShell"}],
        "tray_repair": True,
        "uninstall": True,
        "can_push_agent": True,
    }
    return {
        "asset": {"id": 10, "company_id": 1, "name": "PC"},
        "asset_display_name": "PC",
        "rmm": {**base, **rmm},
        "can_edit": True,
        "customer_safe": False,
        "is_super_admin": True,
    }


RMM_ITEMS = ("data-rmm-remote=", "data-rmm-run-open", "data-rmm-command-open", "data-choco-modal-open", "data-rmm-tray-repair=", "Check in now")


def test_rmm_actions_join_the_grouped_actions_menu_with_icons(header):
    html = header.render(**_values())
    menu = html.split('id="asset-detail-actions"', 1)[1]
    for item in RMM_ITEMS:
        assert item in menu
    assert "RustDesk remote desktop (Prompt)" in menu and 'data-rmm-remote-mode="remote_prompt"' in menu
    assert "RustDesk remote desktop" in menu and 'data-rmm-remote-mode="terminal"' in menu
    headings = [part.split("<", 1)[0] for part in menu.split('class="header-menu__heading" role="presentation">')[1:]]
    assert headings == ["Asset", "Remote access", "RMM", "Agent maintenance", "Go to"]
    # The super admin's destructive actions close the menu in an unlabelled group.
    assert menu.count("header-menu__divider") == len(headings)
    assert menu.count('class="header-menu__icon"') == menu.count('class="header-menu__item')
    assert "Push RMM agent" not in menu
    tail = menu.rsplit("header-menu__divider", 1)[1]
    assert "Uninstall agents" in tail and 'data-asset-uninstall="10"' in tail
    assert "Delete" in tail and 'data-asset-delete-redirect="/assets"' in tail


def test_only_super_admins_get_uninstall_and_delete(header):
    html = header.render(**{**_values(), "is_super_admin": False})
    assert "Uninstall agents" not in html and "data-asset-delete" not in html
    # Without an agent there is nothing to uninstall, but the asset can still be deleted.
    html = header.render(**_values(agent=None, uninstall=False))
    assert "Uninstall agents" not in html and "data-asset-delete" in html


def test_rmm_actions_wait_for_the_agent(header):
    html = header.render(**_values(agent=None, remote_control=[], command_languages=[], tray_repair=False))
    for item in RMM_ITEMS:
        assert item not in html
    assert "Remote access" not in html and ">RMM<" not in html
    # Deploying (and repairing) the agent goes through the tray app, so stays available.
    assert "Push RMM agent" in html and "Repair RMM agent" in html


def test_customer_view_hides_rmm_actions(header):
    html = header.render(**{**_values(), "customer_safe": True, "is_super_admin": False})
    for item in RMM_ITEMS:
        assert item not in html
    assert "Agent maintenance" not in html


def test_meshcentral_functions_stay_in_actions_without_a_connect_shortcut(header):
    providers = [{"provider": "meshcentral", "label": "MeshCentral"}]
    html = header.render(**_values(remote_control=providers))
    assert "Connect with MeshCentral" not in html
    assert 'data-rmm-remote="meshcentral"' not in html.split('id="asset-detail-actions"', 1)[0]
    menu = html.split('id="asset-detail-actions"', 1)[1]
    assert menu.count('data-rmm-remote="meshcentral"') == 3
    assert menu.count('data-rmm-remote-asset="10"') == 3
    for mode, label in (("remote", "remote desktop"), ("file_transfer", "file transfer"), ("terminal", "terminal")):
        assert f'data-rmm-remote-mode="{mode}"' in menu
        assert f"MeshCentral {label}" in menu

    for values in (_values(agent=None, remote_control=providers),
                   {**_values(remote_control=providers), "customer_safe": True}):
        assert 'data-rmm-remote="meshcentral"' not in header.render(**values)


@pytest.mark.parametrize("can_wake,customer_safe,visible", [(True, False, True), (False, False, False), (True, True, False)])
def test_wake_available_without_an_agent_only_to_authorised_staff(header, can_wake, customer_safe, visible):
    html = header.render(**{**_values(agent=None), "can_wake": can_wake, "customer_safe": customer_safe})
    assert ('data-wol-asset-id="10"' in html) is visible
    if visible:
        assert "Wake on LAN" in html and "data-confirm=" in html
