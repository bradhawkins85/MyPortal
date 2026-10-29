"""Exercise the shared navigation with real markup, CSS and JS on touch screens."""
import json
import os
import re
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
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox", "--disable-crash-reporter", "--disable-breakpad"])
        yield instance
        instance.close()


@pytest.mark.parametrize("style", ["top", "sidebar"])
@pytest.mark.parametrize("labels", ["auto", "always"])
def test_mobile_navigation_portrait_landscape_and_desktop(browser, style, labels):
    from playwright.sync_api import expect

    preferences = {"navigation_style": style, "top_menu_labels": labels, "groups": [
        {"id": "__group__:work", "label": "Work", "icon": "folder", "items": ["/chat"]},
    ]}
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True)
    env.globals.update(static_url=lambda path: path, feature_pack_available=lambda name: name == "chat",
                       sidebar_default_preferences=lambda: preferences)
    html = env.get_template("base.html").render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/"})()})(),
        current_user={"id": 42}, active_membership={}, menu_access={"menu.chat": "read"},
        matrix_chat_enabled=True, can_access_chat=True, available_companies=[],
        cart_summary={"item_count": 0, "total_quantity": 0, "subtotal": 0},
        plausible_config={"enabled": False}, csrf_token="test-csrf",
    )
    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def intercept(route):
        path = urlsplit(route.request.url).path
        if path == "/fixture":
            route.fulfill(content_type="text/html", body=html)
        elif path in ("/static/css/app.css", "/static/js/layout.js"):
            route.fulfill(content_type="text/css" if path.endswith(".css") else "application/javascript",
                          body=(ROOT / "app" / path.lstrip("/")).read_text())
        elif path.startswith("/static/"):
            route.fulfill(body="")
        else:
            route.fulfill(content_type="application/json", body=json.dumps(preferences))

    page.route("**/*", intercept)
    try:
        page.goto("http://navigation.test/fixture")
        toggle = page.locator('[data-mobile-nav-toggle]')
        nav = page.locator('[data-sidebar]')
        group = page.get_by_role("button", name="Work", exact=True)
        for width, height in [(390, 844), (844, 390), (1024, 768), (320, 568)]:
            page.set_viewport_size({"width": width, "height": height})
            expect(toggle).to_be_visible()
            expect(nav).to_have_attribute("inert", "")
            toggle.tap()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(nav).not_to_have_attribute("inert", "")
            # Opening the drawer must not focus an input and raise the keyboard.
            expect(nav).to_be_focused()
            expect(group.locator(".menu__label")).to_be_visible()
            if group.get_attribute("aria-expanded") == "true":
                group.tap()
            group.locator(".menu__icon svg").tap()
            expect(group).to_have_attribute("aria-expanded", "true")
            expect(toggle).to_have_attribute("aria-expanded", "true")
            link = nav.locator('a[href="/chat"]')
            expect(link).to_be_visible()
            # Keep the fixture loaded while verifying the real link's click behavior.
            link.evaluate("el => el.addEventListener('click', event => event.preventDefault())")
            link.locator("svg").tap()
            expect(toggle).to_have_attribute("aria-expanded", "false")
            toggle.tap()
            page.keyboard.press("Escape")
            expect(toggle).to_be_focused()
            toggle.tap()
            page.locator('[data-mobile-nav-scrim]').tap(position={"x": width - 10, "y": 10})
            expect(toggle).to_have_attribute("aria-expanded", "false")
            box = nav.bounding_box()
            assert box["width"] <= 281
        # An open drawer must become interactive desktop navigation on resize.
        toggle.tap()
        page.set_viewport_size({"width": 1440, "height": 900})
        expect(toggle).to_be_hidden()
        expect(nav).not_to_have_attribute("inert", "")
        expect(nav).not_to_have_attribute("aria-hidden", "true")
        expect(page.locator("body")).to_have_class(
            re.compile(r".*navigation--top.*") if style == "top" else "theme-default"
        )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(toggle).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(nav).to_have_attribute("inert", "")
        assert not errors
    finally:
        context.close()
