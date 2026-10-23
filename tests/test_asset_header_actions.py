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
    return env.from_string('{% from "macros/header.html" import page_header_actions with context %}' + fragment)


def _values(**rmm):
    base = {
        "can_run": True,
        "agent": {"os": "windows"},
        "remote_control": [{"provider": "rustdesk", "label": "RustDesk"}],
        "command_languages": [{"value": "powershell", "label": "PowerShell"}],
        "tray_repair": True,
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
    assert "RustDesk remote desktop" in menu and 'data-rmm-remote-mode="terminal"' in menu
    headings = [part.split("<", 1)[0] for part in menu.split('class="header-menu__heading" role="presentation">')[1:]]
    assert headings == ["Asset", "Remote access", "RMM", "Agent maintenance", "Go to"]
    assert menu.count("header-menu__divider") == len(headings) - 1
    assert menu.count('class="header-menu__icon"') == menu.count('class="header-menu__item')
    assert "Push RMM agent" not in menu


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
