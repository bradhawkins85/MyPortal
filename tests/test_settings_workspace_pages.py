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


def test_rmm_automation_sections():
    html = _env().get_template("rmm/automation.html").render(
        can_run=True, is_super_admin=False, tags=[], default_timezone="UTC", schedules=[], steps=[],
        onboarding_runs=[], windows_agents=[], agents=[], company={"name": "Contoso"}, csrf_token="tok",
    )
    _assert_workspace(html, ["schedules", "apps", "onboarding", "history"])
    # rmm_scripts.js reads its config from the [data-rmm-page] wrapper.
    assert re.search(r'<div class="[^"]*" data-rmm-page data-rmm-automation-page data-ae-workspace', html)
    # Modals stay outside the sections so they open from any section.
    assert html.index('id="rmm-choco-step-modal"') > html.index("ce-panels")
    without_run = _env().get_template("rmm/automation.html").render(
        can_run=False, is_super_admin=False, tags=[], default_timezone="UTC", schedules=[], steps=[],
        onboarding_runs=[], windows_agents=[], agents=[], company=None, csrf_token="tok",
    )
    assert _sections(without_run) == ["schedules", "onboarding", "history"]


def test_event_log_rules_open_the_editor_or_run_now():
    from app.services import rmm_event_log_rules as rules

    base = dict(company={"name": "Contoso"}, rules=[], can_edit=True, is_super_admin=False, errors={}, conditions=[],
                fields=rules.FIELDS, operators=rules.OPERATORS, field_operators=rules.FIELD_OPERATORS,
                actions=rules.ACTIONS, schedule_modes=rules.SCHEDULE_MODES, devices=[], scripts=[], script=None,
                script_fields=[], variables=[], csrf_token="tok")
    listing = _env().get_template("rmm/event_log_rules.html").render(**base, editing=None, run_now=None)
    _assert_workspace(listing, ["rules"])
    editing = {"id": None, "name": "", "scope": "company", "asset_id": None, "action": "drop", "target_mode": "all",
               "target_rules": None, "is_enabled": True, "cooldown_minutes": 60, "schedule_mode": "delay",
               "delay_minutes": 60, "run_time": "02:00", "timezone": "UTC", "script_id": None, "inputs": {}}
    html = _env().get_template("rmm/event_log_rules.html").render(**base, editing=editing, run_now=None)
    assert _sections(html) == ["rules", "editor"]
    assert 'data-ae-initial="editor" data-ae-form="rmm-event-rule-settings-form"' in html
    assert 'id="rmm-event-rule-settings-form" data-rmm-event-rule-form' in html and "data-ae-savebar" in html


def test_defender_settings_are_sections():
    context = dict(defender_enabled=True, is_super_admin=True, defender_can_write=True, defender_devices=[],
                   defender_detections=[], defender_commands=[], defender_exclusions=[], defender_exclusion_lists=[],
                   defender_companies=[], defender_settings={}, defender_tamper_locked_devices=[], csrf_token="tok")
    html = _env().get_template("defender/index.html").render(**context)
    _assert_workspace(html, ["endpoints", "detections", "actions", "policy", "tickets", "exclusions", "lists"])
    assert "data-defender-modal" not in html
    for hook in ("data-defender-settings-form", 'id="defender-exclusion-form"', "data-exclusion-list-form",
                 'data-table-filter="defender-devices-table"'):
        assert hook in html, hook
    context["is_super_admin"] = False
    assert "lists" not in _sections(_env().get_template("defender/index.html").render(**context))
def _bcp(can_edit: bool) -> str:
    from datetime import datetime

    plan = {"title": "Contoso BCP", "executive_summary": "Keep trading.", "version": "1.2",
            "last_reviewed": datetime(2026, 9, 1), "next_review": None}
    return _env().get_template("bcp/overview.html").render(
        plan=plan, objectives=[{"id": 1, "objective_text": "Restore email in 4 hours"}],
        distribution_list=[{"id": 2, "copy_number": "1", "name": "CEO", "location": "Safe"}],
        kpi_items=[{"label": "RTO coverage", "value": "50%", "variant": "info"}], recent_reviews=[],
        recent_plan_changes=[], dependency_count=3, approved_review_count=1, has_bcp_planning_gaps=True,
        bcp_compliance_help_url="https://example.test/bcp", can_edit=can_edit, csrf_token="tok",
    )


def test_bcp_overview_sections_replace_dialogs():
    html = _bcp(can_edit=True)
    _assert_workspace(html, ["plan", "objectives", "distribution", "reviews", "history", "framework"])
    assert "<dialog" not in html and "onclick=" not in html
    assert 'id="bcp-plan-form"' in html and 'data-ae-form="bcp-plan-form"' in html and "data-ae-savebar" in html
    assert 'action="/bcp/objectives"' in html and 'action="/bcp/distribution"' in html
    assert 'action="/bcp/objectives/1/delete" class="inline-form" data-confirm="Delete this objective?"' in html

    read_only = _bcp(can_edit=False)
    assert 'action="/bcp/update"' not in read_only and "data-ae-savebar" not in read_only
    assert "Keep trading." in read_only


class _Url(str):
    @property
    def path(self) -> str:
        return str(self)


def _shop(**overrides) -> str:
    context = dict(
        request=SimpleNamespace(url_for=lambda name: _Url("/shop"), query_params={}),
        categories=[{"id": 4, "name": "Laptops", "children": []}], products=[], current_category=None,
        show_packages=False, show_featured=False, show_subscriptions=False, subscription_type=None, current_user=None,
        subscription_categories=[{"id": 1, "name": "Licences"}], showing_category_cards=True,
        category_cards=[], show_out_of_stock=False, search_term="", active_membership=None,
        cart_summary={"total_quantity": 2}, total_count=0, csrf_token="tok", can_access_cart=True, is_super_admin=False,
        shop_product_name_min_visible_chars=20, low_stock_threshold=5,
        active_subscription_product_ids=[],
    )
    context.update(overrides)
    return _env().get_template("shop/index.html").render(**context)


def test_shop_nav_links_categories_and_marks_the_current_one():
    html = _shop()
    assert "data-ae-workspace data-ae-link-nav" in html and "asset_edit_sections.js" in html
    assert re.search(r'href="/shop" aria-current="page"><span class="ce-nav__label">All categories', html)
    assert 'href="/shop?category=4"><span class="ce-nav__label">Laptops' in html
    assert 'href="/shop?category=subscriptions"' in html and "2 in your cart" in html

    in_category = _shop(current_category=4, showing_category_cards=False)
    assert re.search(r'href="/shop\?category=4" aria-current="page"', in_category)


ADMIN_SHOP_PAGES = {
    "admin/shop.html": "catalogue", "admin/shop_product_create.html": "catalogue",
    "admin/shop_packages.html": "packages", "admin/shop_package_detail.html": "packages",
    "admin/shop_optional_accessories.html": "accessories", "admin/shop_categories.html": "categories",
    "admin/shop_subscription_categories.html": "subscription-categories",
    "admin/shop_freight_rules.html": "freight",
}


def test_shop_admin_pages_share_the_workspace_nav():
    env = _env()
    for template, key in ADMIN_SHOP_PAGES.items():
        source = (TEMPLATES / template).read_text()
        env.parse(source)  # compiles
        assert f'call shop_admin_workspace("{key}"' in source, template

        assert "import shop_admin_workspace with context" in source, template

    def render(active: str, **context) -> str:
        return env.from_string(
            '{% from "admin/_shop_workspace.html" import shop_admin_workspace with context %}'
            '{% call shop_admin_workspace(active, [{"label": "3 products"}]) %}<p>Body</p>{% endcall %}'
        ).render(active=active, **context)

    categories = [{"id": 4, "name": "Laptops"}, {"id": 5, "name": "Monitors"}]
    html = render("freight", shop_nav_categories=categories)
    assert "data-ae-link-nav" in html and "<p>Body</p>" in html and "3 products" in html
    assert 'href="/admin/shop/freight-rules" aria-current="page"' in html
    assert html.count('aria-current="page"') == 1
    # The same category choices as /shop, without the storefront link.
    for href, label in (("featured", "Featured"), ("subscriptions", "Subscriptions"), ("4", "Laptops"), ("5", "Monitors")):
        assert f'href="/admin/shop?category={href}"><span class="ce-nav__label">{label}<' in html, label
    assert "View the shop" not in html and "Storefront" not in html and 'href="/shop"' not in html
    # Categories come last so a long list never hides the other areas.
    titles = re.findall(r'class="ce-nav__group-title"[^>]*>([^<]+)<', html)
    assert titles == ["Catalogue", "Organisation", "Delivery", "Categories"]

    all_products = render("catalogue", shop_nav_categories=categories)
    assert 'href="/admin/shop" aria-current="page"' in all_products
    laptops = render("catalogue", shop_nav_categories=categories, shop_nav_category=4)
    assert 'href="/admin/shop?category=4" aria-current="page"' in laptops
    assert laptops.count('aria-current="page"') == 1
    featured = render("catalogue", shop_nav_category="featured")
    assert 'href="/admin/shop?category=featured" aria-current="page"' in featured


def test_shop_categories_page_renders_inside_the_workspace():
    html = _env().get_template("admin/shop_categories.html").render(
        categories=[], flat_categories=[], csrf_token="tok", request=SimpleNamespace(query_params={}),
    )
    assert 'href="/admin/shop/categories" aria-current="page"' in html
    assert html.index("ce-panels") < html.index("product-categories-heading")


def test_admin_shop_category_filter_parsing():
    from app.features.shop import handlers

    assert handlers._admin_shop_category_param("12") == 12
    assert handlers._admin_shop_category_param(" Featured ") == "featured"
    assert handlers._admin_shop_category_param("subscriptions") == "subscriptions"
    for value in (None, "", "0", "-3", "packages", "1;DROP"):
        assert handlers._admin_shop_category_param(value) is None, value


def test_admin_shop_page_filters_by_category(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from starlette.requests import Request
    from starlette.responses import HTMLResponse

    from app import main
    from app.features.shop import handlers
    from app.repositories import companies, license_sku_friendly_names, shop, stock_feed, subscription_categories

    products = [
        {"id": 1, "name": "ThinkPad", "sku": "TP", "vendor_sku": None, "subscription_category_id": None},
        {"id": 2, "name": "M365", "sku": "M365", "vendor_sku": None, "subscription_category_id": 3},
    ]
    list_products = AsyncMock(return_value=products)
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(shop, "list_all_categories_flat", AsyncMock(return_value=[]))
    monkeypatch.setattr(shop, "list_categories_with_products", AsyncMock(return_value=[]))
    monkeypatch.setattr(shop, "list_categories", AsyncMock(return_value=[{"id": 4, "name": "Laptops"}]))
    monkeypatch.setattr(shop, "list_products_summary", list_products)
    monkeypatch.setattr(shop, "get_category_descendants", AsyncMock(return_value=[4, 7]))
    monkeypatch.setattr(shop, "list_featured_product_ids", AsyncMock(return_value={1}))
    monkeypatch.setattr(companies, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(subscription_categories, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(license_sku_friendly_names, "list_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(stock_feed, "get_recent_dbp_trends", AsyncMock(return_value={}))
    captured: dict = {}

    async def fake_render(template, request, user, *, extra):
        captured.update(extra)
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render)
    request = Request({"type": "http", "method": "GET", "path": "/admin/shop", "query_string": b"", "headers": []})

    def run(category):
        captured.clear()
        asyncio.run(handlers.admin_shop_page(request, show_archived=False, search="", category=category))
        return captured

    extra = run("4")
    assert list_products.await_args.args[0].category_ids == [4, 7]
    assert extra["shop_nav_category"] == 4
    assert extra["shop_nav_categories"] == [{"id": 4, "name": "Laptops"}]
    assert [p["id"] for p in run("subscriptions")["products"]] == [2]
    assert [p["id"] for p in run("featured")["products"]] == [1]
    everything = run(None)
    assert everything["shop_nav_category"] is None and len(everything["products"]) == 2
    assert list_products.await_args.args[0].category_ids is None
