"""Settings pages laid out as the multi-section settings workspace."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import jinja2

TEMPLATES = Path("app/templates")
STUB_BASE = (
    "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}"
    "{% block scripts %}{% endblock %}"
)


def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [jinja2.DictLoader({"base.html": STUB_BASE}), jinja2.FileSystemLoader(str(TEMPLATES))]
        ),
        autoescape=True,
    )
    env.globals["static_url"] = lambda path: path
    return env


def _sections(html: str) -> list[str]:
    return re.findall(r'data-ae-section="([\w-]+)"', html)


def _assert_workspace(html: str, sections: list[str]) -> None:
    assert "data-ae-workspace" in html and "asset_edit_sections.js" in html
    assert _sections(html) == sections
    for key in sections:
        assert f'data-ae-link="{key}"' in html, key
    # layout.js only confirms forms, so a button-level confirm would be skipped.
    assert not re.search(r"<button[^>]*data-confirm", html)


def _m365(**overrides) -> str:
    health = {
        "variant": "warning", "label": "Setup incomplete", "message": "Finish setup.", "state": "pending",
        "workloads": [{"name": "Mail", "ready": True, "help": "x"}, {"name": "Licenses", "ready": False, "help": "y"}],
        "has_pending_candidate": False, "next_action_url": "/m365/connect", "next_action_label": "Connect",
        "last_verified_at": None, "renewal_at": None, "verification_error": None,
    }
    context = {
        "company": {"name": "Contoso"}, "connection_health": health, "is_super_admin": True,
        "has_credentials": True, "credential": {"tenant_id": "t", "client_id": "c"}, "admin_credential": None,
        "has_admin_credentials": False, "admin_credentials_configured": True, "csrf_token": "tok",
        "request": SimpleNamespace(query_params={}),
    }
    context.update(overrides)
    return _env().get_template("m365/index.html").render(**context)


def test_m365_sections_follow_access():
    html = _m365()
    _assert_workspace(html, ["overview", "setup", "credentials", "provisioning", "permissions", "sync", "signatures"])
    assert 'data-confirm="Delete stored Microsoft 365 credentials?"' in html
    assert ">1/2<" in html  # Workload readiness count in the nav.

    technician = _m365(is_super_admin=False)
    assert _sections(technician) == ["overview", "sync", "signatures"]


def test_notification_settings_sections():
    context = {
        "event_menu": [{"event_type": "a", "display_name": "A", "is_user_visible": True}],
        "preferences": [], "exclusion_rules": [], "default_event_types": [], "channel_descriptions": {},
        "event_settings": {}, "modules": [], "event_settings_endpoint": "/e", "preferences_endpoint": "/p",
        "exclusions_endpoint": "/x", "is_super_admin": True,
    }
    html = _env().get_template("notifications/settings.html").render(**context)
    _assert_workspace(html, ["preferences", "exclusions", "events"])
    # The scripts find their forms and the event picker through these hooks.
    for hook in ("data-notification-admin", "data-notification-event-menu", "data-event-settings-form",
                 "data-notification-preferences-form", "data-exclusions-panel", 'data-table-filter="notification-preferences-table"'):
        assert hook in html, hook

    context["is_super_admin"] = False
    assert _sections(_env().get_template("notifications/settings.html").render(**context)) == ["preferences", "exclusions"]


def test_smtp2go_sections_share_the_settings_form():
    context = {
        "smtp2go_analytics": {"totals": {}, "rates": {}, "queue": {}, "trends": [], "geo_breakdown": [],
                              "device_breakdown": [], "client_breakdown": []},
        "smtp2go_settings": {"ab_campaigns": []}, "smtp2go_trigger_options": [], "smtp2go_campaigns": [],
        "csrf_token": "tok",
    }
    html = _env().get_template("admin/smtp2go.html").render(**context)
    _assert_workspace(html, ["deliverability", "breakdown", "queue", "campaigns", "automation"])
    assert 'data-ae-form="smtp2go-settings-form"' in html
    assert 'name="abCampaignsRaw" form="smtp2go-settings-form"' in html
    assert "data-ae-savebar" in html and 'id="abc-modal"' in html


def test_modules_operations_views_are_sections():
    ops = {
        "summary": {"enabled_modules": 1, "warning_modules": 1}, "credential_alerts": [], "setup_steps": [],
        "dependency_graph": {"conflicts": [], "graph": []}, "health_rows": [],
    }
    modules = [
        {"slug": "xero", "name": "Xero", "description": "", "enabled": True, "settings": {}},
        {"slug": "create-ticket", "name": "Internal", "description": "", "enabled": True, "settings": {}},
    ]
    html = _env().get_template("admin/modules.html").render(
        modules=modules, operations_center=ops, module_webhook_urls={}, csrf_token="tok"
    )
    _assert_workspace(html, ["modules", "health", "credentials", "setup", "dependencies"])
    assert 'id="show-disabled-modules"' in html and 'id="webhook-url-modal"' in html
    assert ">1/1<" in html  # Internal modules are not counted.
