"""Wake actions use the real menus and JS with intercepted network requests."""

import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=executable, args=[
            "--no-sandbox", "--disable-crash-reporter", "--disable-breakpad",
        ])
        yield instance
        instance.close()


def _markup(location):
    templates = ROOT / "app/templates"
    env = Environment(loader=FileSystemLoader(templates), autoescape=True)
    env.globals["static_url"] = lambda path: path
    asset = {"id": 10, "company_id": 1, "name": "Offline PC"}
    if location == "header":
        source = (templates / "assets/detail.html").read_text()
        source = source.split("{% block header_actions %}", 1)[1].split("{% endblock %}", 1)[0]
        source = '{% from "macros/header.html" import page_header_actions with context %}' + source
        source += '<p id="wol-status-10" aria-live="polite"></p>'
    else:
        source = (templates / "assets/index.html").read_text()
        source = source.split("{% block content %}", 1)[1].split("{% endblock %}", 1)[0]
        source = ('{% from "macros/header.html" import page_header_overflow with context %}'
                  '{% import "assets/_action_icons.html" as action_icons %}' + source)
    return env.from_string(source).render(
        asset=asset, assets=[asset], has_assets=True, columns=[], stats={}, rmm=None,
        is_super_admin=True, can_edit=True, can_write_assets=True, can_wake=True,
        customer_safe=False, asset_display_name="Offline PC",
    )


@pytest.mark.parametrize("location", ["row", "header"])
@pytest.mark.parametrize("status,expected,http_status", [
    ("dispatched", "Wake dispatched", 200),
    ("no_mac", "No MAC address", 200),
    ("no_candidates", "No online agents", 200),
    ("error", "Company access required", 403),
])
def test_wake_menu_reports_results_outside_closed_menu(browser, location, status, expected, http_status):
    page = browser.new_page(viewport={"width": 768, "height": 1024})
    errors = []
    calls = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = (_markup(location)
            + '<script src="/static/js/header_menu.js"></script>'
            + '<script src="/static/js/wol.js"></script>')

    def route(request):
        path = urlsplit(request.request.url).path
        if path.startswith("/static/"):
            request.fulfill(path=str(ROOT / "app" / path.lstrip("/")))
        elif path.endswith("/wake"):
            calls.append((path, request.request.method))
            request.fulfill(status=http_status, content_type="application/json", body=json.dumps({
                "status": status, "delivered_count": 1, "candidate_count": 1,
                "detail": "Company access required",
            }))
        elif path.endswith("/attempts"):
            request.fulfill(content_type="application/json", body='{"stats": {}}')
        else:
            request.fulfill(content_type="text/html", body=html)

    page.route("**/*", route)
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        page.goto("http://wol.test/")
        page.get_by_role("button", name="Actions", exact=True).click()
        wake = page.get_by_role("button", name="Wake on LAN", exact=True)
        wake.focus()
        wake.press("Enter")
        page.wait_for_function("text => document.getElementById('wol-status-10').textContent.includes(text)", arg=expected)
        assert page.locator("#wol-status-10").is_visible()
        assert not page.locator("[data-header-menu-panel]").is_visible()
        page.wait_for_function("!document.querySelector('[data-wol-asset-id]').disabled")
        assert page.locator("[data-wol-asset-id] svg").count() == 1
        assert calls == [("/api/wol/assets/10/wake", "POST")]
        assert errors == []
    finally:
        page.close()
