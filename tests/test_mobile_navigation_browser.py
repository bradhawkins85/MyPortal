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


@pytest.mark.parametrize("screen_height", [1080, 1440])
def test_top_menu_labels_follow_available_space(browser, screen_height):
    from playwright.sync_api import expect

    preferences = {"navigation_style": "top", "top_menu_labels": "auto"}
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True)
    env.globals.update(static_url=lambda path: path, feature_pack_available=lambda name: False,
                       sidebar_default_preferences=lambda: preferences)
    html = env.get_template("base.html").render(
        request=type("Request", (), {"url": type("Url", (), {"path": "/"})()})(),
        app_name="MyPortal", current_user={"id": 42}, active_membership={}, menu_access={},
        available_companies=[], cart_summary={"item_count": 0, "total_quantity": 0, "subtotal": 0},
        plausible_config={"enabled": False}, csrf_token="test-csrf",
    )
    context = browser.new_context(viewport={"width": 1366, "height": 768},
                                  screen={"width": 2560, "height": screen_height})
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
        body = page.locator("body")
        compact = re.compile(r".*navigation--top-compact.*")
        expect(body).to_have_class(re.compile(r".*navigation--top.*"))
        expect(body).not_to_have_class(compact)
        home = page.locator('.menu > .menu__item > a[href="/"]')
        expect(home.locator('.menu__label')).to_be_visible()

        # Content changes at a fixed viewport must trigger measurement too.
        page.evaluate('''() => {
            const menu = document.querySelector('.layout__sidebar .menu');
            const source = menu.querySelector('.menu__item');
            const badge = document.createElement('span');
            badge.className = 'menu__badge';
            badge.textContent = '12';
            source.querySelector('.menu__label').appendChild(badge);
            for (let i = 0; i < 10; i++) {
                const item = source.cloneNode(true);
                item.dataset.testExtra = 'true';
                item.dataset.menuLabel = 'Workspace ' + i;
                item.querySelector('a').href = '/workspace-' + i;
                item.querySelector('.menu__label').textContent = 'Workspace ' + i;
                menu.appendChild(item);
            }
        }''')
        expect(body).to_have_class(compact)
        expect(home.locator('.menu__label')).to_be_hidden()
        expect(home).to_have_attribute('title', 'Dashboard')
        expect(home).to_have_accessible_name('Dashboard')
        expect(home.locator(':scope > .menu__badge')).to_be_visible()

        # Widen, narrow, and widen again to catch stale compact measurements.
        for width, is_compact in [(2560, False), (1366, True), (2560, False)]:
            page.set_viewport_size({"width": width, "height": 900})
            if is_compact:
                expect(body).to_have_class(compact)
            else:
                expect(body).not_to_have_class(compact)
                expect(home.locator('.menu__label .menu__badge')).to_be_visible()
                assert home.get_attribute('title') is None

        # A wider sibling consumes menu space without a viewport change.
        page.locator('.brand').evaluate("el => el.style.width = '1400px'")
        expect(body).to_have_class(compact)
        page.locator('.brand').evaluate("el => el.style.width = ''")
        expect(body).not_to_have_class(compact)

        page.set_viewport_size({"width": 1366, "height": 768})
        expect(body).to_have_class(compact)
        # Hiding entries restores names without changing window dimensions.
        page.locator('[data-test-extra]').evaluate_all('items => items.forEach(item => item.hidden = true)')
        expect(body).not_to_have_class(compact)
        page.locator('[data-test-extra]').evaluate_all('items => items.forEach(item => item.hidden = false)')
        expect(body).to_have_class(compact)

        # The explicit user preference still takes precedence over auto fitting.
        page.evaluate("MyPortalSidebarMenu.apply({navigation_style: 'top', top_menu_labels: 'always'})")
        expect(body).not_to_have_class(compact)
        expect(home.locator('.menu__label')).to_be_visible()
        assert not errors
    finally:
        context.close()
