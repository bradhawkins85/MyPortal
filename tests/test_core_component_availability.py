"""Deployment kill switches for core components listed on the feature pack page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.core.core_components import CORE_COMPONENT_SLUGS, components_for_path
from app.services.component_availability import (
    ComponentAvailability,
    configure_component_availability,
)


EXPECTED_SLUGS = {
    "network_devices",
    "ipam",
    "racks",
    "defender",
    "office365",
    "shared_credentials",
    "backup_history",
    "backup_summary",
    "rag_index",
    "ai_quality",
    "ai_tag_synonyms",
    "tray",
    "outlook_contacts",
    "notification_contact",
    "email_signature",
    "click_to_call",
    "gmp_glp",
    "essential8",
    "forms",
}


@pytest.fixture(autouse=True)
def reset_availability():
    yield
    configure_component_availability(known_feature_packs=(), known_modules=())


def test_registry_lists_every_requested_component():
    assert set(CORE_COMPONENT_SLUGS) == EXPECTED_SLUGS


def test_core_component_slugs_are_accepted_by_configuration():
    policy = configure_component_availability(
        disabled_feature_packs="ipam,tray",
        known_feature_packs=("assets",),
        known_modules=(),
    )
    assert not policy.feature_pack_available("ipam")
    assert not policy.feature_pack_available("tray")
    assert policy.feature_pack_available("racks")


def test_settings_accept_core_component_slugs(monkeypatch):
    monkeypatch.setenv("DISABLED_FEATURE_PACKS", "office365,rag_index")
    settings = Settings()
    assert settings.disabled_feature_packs == "office365,rag_index"


def test_path_matching_is_segment_aware():
    assert [c.slug for c in components_for_path("/devices")] == ["network_devices"]
    assert {c.slug for c in components_for_path("/devices/ipam-import")} == {
        "network_devices",
        "ipam",
    }
    assert components_for_path("/devices-report") == ()
    assert components_for_path("/tickets") == ()


def test_disabled_component_blocks_only_its_paths():
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"defender"}))
    assert not policy.path_available("/defender")
    assert not policy.path_available("/api/tray/defender/status")
    assert policy.path_available("/api/tray/heartbeat")
    assert policy.path_available("/tickets")


def test_parent_pack_disables_component():
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"assets"}))
    assert not policy.feature_pack_available("racks")
    assert not policy.path_available("/ipam")


def test_middleware_returns_404_for_disabled_component(monkeypatch):
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"ai_quality"}))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    client = TestClient(main_module.app)
    response = client.get("/admin/ai-quality", follow_redirects=False)
    assert response.status_code == 404


def _render_sidebar(monkeypatch, *disabled: str) -> str:
    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    request = SimpleNamespace(url=SimpleNamespace(path="/", query=""))
    return main_module.templates.env.get_template("base.html").render(
        request=request,
        app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True},
        is_super_admin=True,
        active_membership={},
        active_company_id=1,
        available_companies=[],
        module_enabled={},
        enabled_module_slugs=[],
        matrix_chat_enabled=True,
        can_access_m365_spam_purge=True,
    )


SIDEBAR_LINKS = (
    "/myforms",
    "/devices",
    "/ipam",
    "/racks",
    "/defender",
    "/m365",
    "/licenses",
    "/shared-credentials",
    "/admin/backup-jobs",
    "/admin/backup-summary",
    "/admin/rag",
    "/admin/ai-quality",
    "/admin/chat/ai-tag-synonyms",
    "/admin/tray/configurations",
)


def test_sidebar_shows_core_components_by_default(monkeypatch):
    body = _render_sidebar(monkeypatch)
    for path in SIDEBAR_LINKS:
        assert f'href="{path}"' in body, path


def test_sidebar_hides_disabled_core_components(monkeypatch):
    body = _render_sidebar(monkeypatch, *EXPECTED_SLUGS)
    for path in SIDEBAR_LINKS:
        assert f'href="{path}"' not in body, path
    assert "Office 365" not in body
    assert 'href="/m365/best-practices"' not in body


def _render(monkeypatch, template: str, *disabled: str, **context) -> str:
    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    request = SimpleNamespace(url=SimpleNamespace(path="/", query=""), query_params={})
    return main_module.templates.env.get_template(template).render(
        request=request,
        app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True, "email_signature": ""},
        is_super_admin=True,
        has_authenticated_user=True,
        active_membership={},
        available_companies=[],
        module_enabled={},
        enabled_module_slugs=[],
        **context,
    )


PROFILE_CARDS = {
    "outlook_contacts": "Outlook contacts</h2>",
    "click_to_call": "Click to call</h2>",
    "notification_contact": "Notification Contact</h2>",
    "email_signature": "Email signature</h2>",
}


def _render_profile(monkeypatch, *disabled: str) -> str:
    return _render(
        monkeypatch,
        "admin/profile.html",
        *disabled,
        profile_show_technician_tools=True,
        profile_m365_contacts={"connected": False},
        profile_totp_devices=[],
        profile_passkeys=[],
    )


def test_profile_cards_render_by_default(monkeypatch):
    body = _render_profile(monkeypatch)
    for marker in PROFILE_CARDS.values():
        assert marker in body
    assert "click_to_call.js" in body


@pytest.mark.parametrize("slug", sorted(PROFILE_CARDS))
def test_profile_card_hidden_when_disabled(monkeypatch, slug):
    body = _render_profile(monkeypatch, slug)
    assert PROFILE_CARDS[slug] not in body
    for other, marker in PROFILE_CARDS.items():
        if other != slug:
            assert marker in body
    if slug == "click_to_call":
        assert "click_to_call.js" not in body


def test_profile_api_paths_blocked():
    policy = ComponentAvailability(
        disabled_feature_packs=frozenset({"outlook_contacts", "click_to_call"})
    )
    assert not policy.path_available("/admin/profile/m365-contacts/connect")
    assert not policy.path_available("/api/profile/m365-contacts/phones")
    assert not policy.path_available("/api/click-to-call/settings")
    assert policy.path_available("/admin/profile")


def test_gmp_glp_filter_clause(monkeypatch):
    from app.repositories import compliance_checks as repo

    enabled = ComponentAvailability()
    disabled = ComponentAvailability(disabled_feature_packs=frozenset({"gmp_glp"}))
    import app.services.component_availability as availability_module

    monkeypatch.setattr(availability_module, "get_component_availability", lambda: enabled)
    assert repo._hidden_category_clause("cat") is None
    monkeypatch.setattr(availability_module, "get_component_availability", lambda: disabled)
    assert repo._hidden_category_clause("cat") == "cat.code NOT IN ('GMP', 'GLP')"


def test_gmp_glp_list_checks_query_excludes_categories(monkeypatch):
    import asyncio

    from app.repositories import compliance_checks as repo
    import app.services.component_availability as availability_module

    disabled = ComponentAvailability(disabled_feature_packs=frozenset({"gmp_glp"}))
    monkeypatch.setattr(availability_module, "get_component_availability", lambda: disabled)
    captured = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        return []

    monkeypatch.setattr(repo.db, "fetch_all", fake_fetch_all)
    asyncio.run(repo.list_checks())
    assert "cat.code NOT IN ('GMP', 'GLP')" in captured["query"]
    asyncio.run(repo.list_categories())
    assert "cat.code NOT IN ('GMP', 'GLP')" in captured["query"]


def test_essential8_paths_blocked_without_touching_compliance_checks():
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"essential8"}))
    assert not policy.path_available("/compliance")
    assert not policy.path_available("/compliance/control/3")
    assert not policy.path_available("/api/essential8/controls")
    assert not policy.path_available("/admin/marketing/essential8-help-links")
    assert policy.path_available("/compliance-checks")
    assert policy.path_available("/admin/compliance-checks/library")
    assert policy.path_available("/api/compliance-checks/checks")


def test_essential8_sidebar_link_hidden(monkeypatch):
    body = _render_sidebar(monkeypatch)
    assert 'href="/compliance"' in body
    body = _render_sidebar(monkeypatch, "essential8")
    assert 'href="/compliance"' not in body
    assert 'href="/compliance-checks"' in body


def test_essential8_report_queries_hidden(monkeypatch):
    import asyncio

    from app.services import company_report_layout
    import app.services.component_availability as availability_module

    async def fake_list_queries():
        return [
            {"slug": "report-essential-8-compliance-progress"},
            {"slug": "stat-strip-report-essential-8"},
            {"slug": "report-licenses"},
        ]

    monkeypatch.setattr(company_report_layout.reporting_repo, "list_queries", fake_list_queries)
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"essential8"}))
    monkeypatch.setattr(availability_module, "get_component_availability", lambda: policy)
    slugs = [q["slug"] for q in asyncio.run(company_report_layout.available_queries())]
    assert slugs == ["report-licenses"]


def test_forms_paths_blocked():
    policy = ComponentAvailability(disabled_feature_packs=frozenset({"forms"}))
    for path in ("/myforms", "/myforms/admin/edit", "/forms", "/admin/forms", "/api/forms/1"):
        assert not policy.path_available(path), path
    assert policy.path_available("/formsets")
