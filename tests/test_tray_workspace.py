"""The tray settings workspace (/admin/tray) and its redirects from old URLs."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jinja2
import pytest
from starlette.datastructures import URL
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main

TEMPLATES = Path("app/templates")
STUB_BASE = (
    "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}"
    "{% block scripts %}{% endblock %}"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [jinja2.DictLoader({"base.html": STUB_BASE}), jinja2.FileSystemLoader(str(TEMPLATES))]
        ),
        autoescape=True,
    )
    env.globals["static_url"] = lambda path: path
    return env


def _tray(**overrides: Any) -> dict[str, Any]:
    tray: dict[str, Any] = {
        "devices": {
            "devices": [
                {"id": 1, "hostname": "PC-1", "device_uid": "u1", "status": "active"},
                {"id": 2, "hostname": "PC-2", "device_uid": "u2", "status": "revoked"},
            ],
            "filters": {"search": "", "status": ""},
            "matrix_enabled": False,
            "tray_release": {"version": "1.2.3", "loaded_assets": []},
            "outdated_device_count": 0,
        },
        "tokens": {
            "tokens": [], "show_revoked": False, "hidden_revoked_count": 2, "companies": [],
            "new_token": None, "now_iso": "", "portal_url": "https://p", "trmm_token_field_name": "F",
        },
        "links": {
            "links": [], "companies": [], "show_revoked": False, "hidden_revoked_count": 0,
            "release_tag": "v1.2.3", "macos_available": True, "expiry_choices": [(30, "30 days")],
            "default_expiry": 30,
        },
        "configurations": {"configurations": [{"id": 4, "name": "Default", "scope": "global",
                                               "enabled": True, "version": 2, "updated_at": ""}]},
        "questions": {"questions": [], "companies": [], "success_message": "Question created.",
                      "error_message": None},
        "versions": {"versions": [], "server_installers": []},
        "diagnostics": {"bundles": []},
        "branding": {"current_path": None, "tooltip_name": "", "default_tooltip_name": "MyPortal Helpdesk"},
    }
    tray.update(overrides)
    return tray


def _render(*, super_admin: bool = True, query: str = "", **overrides: Any) -> str:
    tray = _tray(**overrides)
    if not super_admin:
        tray = {"devices": tray["devices"]}
    return _env().get_template("admin/tray/index.html").render(
        tray=tray,
        current_user={"is_super_admin": super_admin},
        csrf_token="t",
        request=SimpleNamespace(url=URL(f"https://portal.test/admin/tray{query}")),
    )


SECTIONS = (
    "devices", "diagnostics", "deployment-urls", "install-tokens", "versions",
    "menu-configurations", "ticket-questions", "tray-icon", "clean-up",
)


def test_workspace_renders_every_section_with_a_nav_link():
    html = _render()

    assert "data-ae-workspace" in html
    for key in SECTIONS:
        assert f'href="#{key}" data-ae-link="{key}"' in html, key
        assert f'id="{key}" data-ae-section="{key}"' in html, key
    assert "asset_edit_sections.js" in html
    # The old pages' Actions-menu links are now sections, not separate pages.
    assert 'href="/admin/tray/devices"' not in html
    assert 'href="/admin/tray/install-tokens"' not in html
    assert "Question created." in html
    # layout.js confirms forms, so destructive buttons rely on their form's data-confirm.
    assert not re.search(r"<button[^>]*data-confirm", html)
    assert 'action="/admin/tray/devices/bulk-delete-revoked" class="form" data-confirm=' in html


def test_workspace_toggles_keep_the_section_and_drop_one_time_tokens():
    html = _render(query="?new_token=secret&search=pc")

    toggle = re.search(r'href="([^"]+)#install-tokens" aria-pressed="false"', html)
    assert toggle, html
    assert "new_token" not in toggle.group(1)
    assert "search=pc" in toggle.group(1)
    assert "show_revoked_tokens=true" in toggle.group(1)


def test_technicians_see_only_the_device_section():
    html = _render(super_admin=False)

    assert 'data-ae-section="devices"' in html
    for key in SECTIONS[1:]:
        assert f'data-ae-section="{key}"' not in html, key
    # Device actions post to super-admin-only routes, so technicians get none.
    assert "/admin/tray/devices/1/revoke" not in html
    assert "/admin/tray/devices/2/delete" not in html


def test_configuration_editor_is_a_sectioned_workspace_with_a_save_bar():
    html = _env().get_template("admin/tray/configuration_form.html").render(
        heading="Edit configuration: Default",
        form_action="/admin/tray/configurations/4",
        config={"name": "Default", "scope": "global", "enabled": True, "payload_json": "[]"},
        trmm_scripts=[], rmm_scripts=[], companies=[], csrf_token="t",
    )

    assert 'data-ae-form="tray-config-form"' in html
    for key in ("general", "menu-items", "display-text", "environment", "branding"):
        assert f'data-ae-section="{key}"' in html, key
    for name in ("name", "scope", "scope_ref_id", "enabled", "display_text", "env_allowlist",
                 "branding_icon_url", "payload_json"):
        assert re.search(rf'name="{name}" form="tray-config-form"', html), name
    assert "data-ae-savebar" in html and "data-ae-discard" in html
    assert "ae:discarded" in html


def _request(path: str, query: str = "") -> Request:
    scope = {
        "type": "http", "method": "GET", "path": path, "query_string": query.encode(),
        "headers": [], "scheme": "http", "server": ("testserver", 80),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "path", "query", "location"),
    [
        ("admin_tray_devices_page", "/admin/tray/devices", "search=pc&status=active",
         "/admin/tray?search=pc&status=active#devices"),
        ("admin_tray_install_tokens_page", "/admin/tray/install-tokens", "show_revoked=true",
         "/admin/tray?show_revoked_tokens=true#install-tokens"),
        ("admin_tray_deployment_links_page", "/admin/tray/deployment-links", "show_revoked=true",
         "/admin/tray?show_revoked_links=true#deployment-urls"),
        ("admin_tray_configurations_page", "/admin/tray/configurations", "", "/admin/tray#menu-configurations"),
        ("admin_tray_diagnostics_page", "/admin/tray/diagnostics", "", "/admin/tray#diagnostics"),
        ("admin_tray_versions_page", "/admin/tray/versions", "", "/admin/tray#versions"),
        ("admin_tray_branding_page", "/admin/tray/branding", "", "/admin/tray#tray-icon"),
        ("admin_tray_ticket_questions_page", "/admin/tray/ticket-questions", "success=Saved",
         "/admin/tray?success=Saved#ticket-questions"),
    ],
)
async def test_old_tray_pages_redirect_to_their_section(handler, path, query, location):
    response = await getattr(main, handler)(_request(path, query))

    assert response.status_code == 303
    assert response.headers["location"] == location


@pytest.mark.anyio
async def test_workspace_loads_only_devices_for_technicians(monkeypatch):
    monkeypatch.setattr(
        main, "_require_authenticated_user",
        AsyncMock(return_value=({"id": 2, "is_helpdesk_technician": True}, None)),
    )
    monkeypatch.setattr(main, "_tray_devices_context", AsyncMock(return_value={"devices": []}))
    tokens = AsyncMock()
    monkeypatch.setattr(main, "_tray_install_tokens_context", tokens)
    captured: dict[str, Any] = {}

    async def fake_render(template_name, request, user, *, extra):
        captured.update(template=template_name, extra=extra)
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render)

    await main.admin_tray_workspace_page(_request("/admin/tray"))

    assert captured["template"] == "admin/tray/index.html"
    assert set(captured["extra"]["tray"]) == {"devices"}
    tokens.assert_not_called()


@pytest.mark.anyio
async def test_workspace_redirects_other_users_home(monkeypatch):
    monkeypatch.setattr(
        main, "_require_authenticated_user", AsyncMock(return_value=({"id": 3}, None))
    )

    response = await main.admin_tray_workspace_page(_request("/admin/tray"))

    assert response.status_code == 303
    assert response.headers["location"] == "/"
