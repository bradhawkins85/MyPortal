"""Browser behavior for the public tray installer tutorial.

Run with RUN_BROWSER_TESTS=1 and system Chromium (or set
PLAYWRIGHT_CHROMIUM_EXECUTABLE). Requests are intercepted locally, so these
checks need neither a running portal nor external network access.
"""

import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
import pytest


ROOT = Path(__file__).resolve().parents[1]
CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run the optional browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required for the optional browser checks")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable,
            args=["--no-sandbox", "--disable-crash-reporter", "--disable-breakpad"],
        )
        yield browser
        browser.close()


@pytest.fixture
def mount(browser):
    contexts = []
    errors = []

    def create(*, user_agent=CHROME, brands=None, java_script_enabled=True, tutorial=True):
        environment = Environment(
            loader=FileSystemLoader(ROOT / "app/templates"), autoescape=select_autoescape(),
        )
        body = environment.get_template("tray/_download_help.html").render(
            app_name="MyPortal", static_url=lambda path: path,
        ) if tutorial else "<p>Another public page</p>"
        html = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="/static/css/tray_download_help.css">'
            '<script defer src="/static/js/tray_download_help.js"></script>'
            f"</head><body>{body}</body></html>"
        )
        context = browser.new_context(
            user_agent=user_agent, java_script_enabled=java_script_enabled,
        )
        contexts.append(context)
        # Override client hints so the fixture does not silently use the real
        # Chromium version's brands when testing a different user agent.
        agent_data = None if brands is None else {"brands": brands}
        context.add_init_script(
            "Object.defineProperty(navigator, 'userAgentData', "
            f"{{ configurable: true, value: {json.dumps(agent_data)} }});"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == "/deploy/browser-test":
                route.fulfill(status=200, content_type="text/html", body=html)
            elif path.startswith("/static/"):
                asset = ROOT / "app" / path.lstrip("/")
                mime = {
                    ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
                }.get(asset.suffix, "application/octet-stream")
                if asset.is_file():
                    route.fulfill(status=200, content_type=mime, body=asset.read_bytes())
                else:
                    route.fulfill(status=404, body="Missing local asset")
            else:
                route.abort()

        context.route("**/*", intercept)
        page.goto("http://tray.test/deploy/browser-test")
        if tutorial:
            page.locator("[data-tray-download-help] > summary").click()
        return page

    yield create
    for context in contexts:
        context.close()
    assert not errors, errors


@pytest.mark.parametrize(
    "user_agent, brands, expected",
    [
        # Both Edge UA and Edge client hints must beat the embedded Chrome identity.
        (CHROME + " Edg/140.0.0.0", None, "edge"),
        (CHROME + " EdgA/140.0.0.0", None, "edge"),
        ("Mozilla/5.0 (iPhone) CriOS/140.0 EdgiOS/140.0", None, "edge"),
        (CHROME, [{"brand": "Google Chrome"}, {"brand": "Microsoft Edge"}], "edge"),
        ("Reduced agent", [{"brand": "Chromium"}, {"brand": "Microsoft Edge"}], "edge"),
        (CHROME, None, "chrome"),
        ("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 CriOS/140.0 Mobile Safari/604.1", None, "chrome"),
        ("Reduced agent", [{"brand": "Not A Brand"}, {"brand": "Google Chrome"}], "chrome"),
        (SAFARI, None, "all"),
        ("Mozilla/5.0 Firefox/140.0", None, "all"),
        ("Unidentified browser", None, "all"),
        ("Reduced agent", [{"brand": "Chromium"}], "all"),
        (CHROME + " OPR/120.0", None, "all"),
        (CHROME + " SamsungBrowser/28.0", None, "all"),
        (CHROME + " Vivaldi/7.0", None, "all"),
        (CHROME + " YaBrowser/25.0", [{"brand": "Google Chrome"}], "all"),
    ],
    ids=[
        "edge-desktop", "edge-android", "edge-ios", "edge-brands-before-chrome",
        "edge-reduced-agent", "chrome-desktop", "chrome-ios", "chrome-client-hints",
        "safari", "firefox", "unknown", "chromium-only", "opera", "samsung", "vivaldi", "yandex",
    ],
)
def test_detects_browser_and_shows_matching_instructions(mount, user_agent, brands, expected):
    page = mount(user_agent=user_agent, brands=brands)
    assert page.locator("#tray-download-browser").input_value() == expected
    assert page.locator("[data-download-browser-controls]").is_visible()
    assert page.locator('[data-download-browser="edge"]').is_visible() == (expected != "chrome")
    assert page.locator('[data-download-browser="chrome"]').is_visible() == (expected != "edge")
    assert page.locator("[data-download-browser-status]").inner_text()
    # Running the installer is common to both browser guides and always remains available.
    assert page.locator("#tray-download-run-title").is_visible()


def test_native_browser_selector_can_override_detection_and_show_both_guides(mount):
    page = mount(user_agent=CHROME + " Edg/140.0.0.0")
    selector = page.locator("#tray-download-browser")
    for selected, visible in [("chrome", {"chrome"}), ("edge", {"edge"}), ("all", {"edge", "chrome"})]:
        selector.select_option(selected)
        for browser_name in ["edge", "chrome"]:
            assert page.locator(f'[data-download-browser="{browser_name}"]').is_visible() == (browser_name in visible)
        status = page.locator("[data-download-browser-status]").inner_text()
        assert {"chrome": "Google Chrome", "edge": "Microsoft Edge", "all": "both"}[selected] in status
        assert page.locator("#tray-download-run-title").is_visible()


def test_without_javascript_both_guides_and_images_remain_available(mount):
    page = mount(java_script_enabled=False)
    assert page.locator("[data-download-browser-controls]").is_hidden()
    for browser_name in ["edge", "chrome"]:
        assert page.locator(f'[data-download-browser="{browser_name}"]').is_visible()
    assert page.locator("#tray-download-run-title").is_visible()
    assert page.locator("[data-tray-download-help] img").count() == 4


def test_script_is_inert_on_pages_without_a_tutorial(mount):
    page = mount(tutorial=False)
    assert page.locator("body").inner_text() == "Another public page"
    assert page.locator("[data-download-browser-controls]").count() == 0
