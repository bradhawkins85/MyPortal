"""Regression tests for deployment-disabled feature packs in the sidebar."""

from types import SimpleNamespace

import app.main as main_module
from app.services.component_availability import ComponentAvailability


def _render_sidebar(monkeypatch, *disabled_packs: str) -> str:
    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled_packs))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    request = SimpleNamespace(url=SimpleNamespace(path="/", query=""))
    return main_module.templates.env.get_template("base.html").render(
        request=request,
        app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True},
        is_super_admin=True,
        active_membership={},
        available_companies=[],
        module_enabled={},
        enabled_module_slugs=[],
        matrix_chat_enabled=True,
        can_view_bcp=True,
        can_access_m365_spam_purge=True,
    )


def test_enabled_feature_pack_links_are_rendered(monkeypatch):
    body = _render_sidebar(monkeypatch)

    assert 'href="/knowledge-base"' in body
    assert 'href="/tickets"' not in body
    assert 'href="/admin/tickets"' in body
    assert 'href="/admin/backup-jobs"' in body
    assert 'href="/admin/api-keys"' in body


def test_disabled_feature_pack_links_are_not_rendered(monkeypatch):
    body = _render_sidebar(
        monkeypatch,
        "api_keys",
        "backups",
        "companies",
        "continuity",
        "knowledge_base",
        "m365_admin",
        "message_templates",
        "tickets",
        "webhooks",
    )

    for path in (
        "/admin/api-keys",
        "/admin/backup-jobs",
        "/admin/companies",
        "/admin/message-templates",
        "/admin/tickets",
        "/admin/webhooks",
        "/bcp",
        "/knowledge-base",
        "/m365/out-of-office",
        "/m365/signatures",
        "/m365/spam-purge",
    ):
        assert f'href="{path}"' not in body
