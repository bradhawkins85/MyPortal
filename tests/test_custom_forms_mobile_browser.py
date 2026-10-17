"""Custom forms fit phone screens; RUN_BROWSER_TESTS=1 uses a local Chromium.

Render the production form template with its real styles and check that every
question stays inside the viewport, both on public share links and in the portal.
"""

import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
import pytest


ROOT = Path(__file__).resolve().parents[1]
PORTAL_BASE = """<!DOCTYPE html><html><head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/css/app.css">
  {% block styles %}{% endblock %}</head><body class="theme-default">
  <div class="layout"><main class="layout__main"><section class="layout__content">
  {% block content %}{% endblock %}</section></main></div>{% block scripts %}{% endblock %}</body></html>"""
RISK_ROWS = ["Almost certain", "Likely", "Possible", "Unlikely", "Rare"]
RISK_COLUMNS = ["Insignificant", "Minor", "Moderate", "Major", "Catastrophic"]
FIELDS = [
    {"id": 1, "label": "Single Line Input", "field_type": "text", "required": False, "config": {}},
    {"id": 2, "label": "Audio", "field_type": "audio", "required": False, "config": {"max_clips": 5}},
    {"id": 3, "label": "Risk", "field_type": "matrix", "required": True,
     "config": {"mode": "table", "rows": RISK_ROWS, "columns": RISK_COLUMNS}},
    {"id": 4, "label": "Service", "field_type": "matrix", "required": False,
     "config": {"rows": ["Speed", "Quality"], "columns": ["Poor", "Fair", "Good", "Very good", "Excellent"]}},
    {"id": 5, "label": "Signature", "field_type": "signature", "required": False, "config": {}},
    {"id": 6, "label": "Rating", "field_type": "rating", "required": False, "config": {"max": 10}},
    {"id": 7, "label": "Choice", "field_type": "radio", "required": False,
     "options": ["Supercalifragilisticexpialidocious-option-without-any-spaces"], "config": {}},
    {"id": 8, "label": "Evidence", "field_type": "file", "required": False, "config": {}},
]


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def mount(browser):
    contexts = []

    def create(*, width, public_share):
        environment = Environment(loader=ChoiceLoader([
            DictLoader({"base.html": PORTAL_BASE}),
            FileSystemLoader(ROOT / "app/templates"),
        ]), autoescape=select_autoescape())
        html = environment.get_template("forms/submit.html").render(
            form={"name": "Test", "slug": "test", "description": None}, title="Test",
            fields=FIELDS, errors=[], static_url=lambda path: path,
            public_share=public_share, submit_action="/forms/share/token",
        )
        # A mobile context widens the layout viewport to fit overflowing content, as phones do.
        context = browser.new_context(viewport={"width": width, "height": 800}, is_mobile=True, has_touch=True)
        contexts.append(context)
        page = context.new_page()

        def intercept(route):
            path = urlsplit(route.request.url).path
            asset = ROOT / "app" / path.lstrip("/")
            if path.startswith("/static/") and asset.is_file():
                route.fulfill(content_type="text/css" if asset.suffix == ".css" else "text/javascript", body=asset.read_bytes())
            elif path.startswith("/static/"):
                route.fulfill(status=204)
            else:
                route.fulfill(content_type="text/html", body=html)

        context.route("**/*", intercept)
        page.goto("https://portal.test/forms/test")
        return page

    yield create
    for context in contexts:
        context.close()


@pytest.mark.parametrize("public_share", [True, False])
@pytest.mark.parametrize("width", [320, 390])
def test_form_fits_phone_width(mount, width, public_share):
    page = mount(width=width, public_share=public_share)
    assert page.evaluate("innerWidth") == width
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    outside = page.evaluate("""() => [...document.querySelectorAll('.cf-public *')]
        .filter((element) => element.getClientRects().length && !element.closest('.sr-only'))
        .filter((element) => { const box = element.getBoundingClientRect(); return box.left < -1 || box.right > innerWidth + 1; })
        .map((element) => element.tagName + '.' + element.className)""")
    assert outside == []


@pytest.mark.parametrize("width", [320, 390])
def test_matrix_keeps_grid_on_phones_and_can_be_answered(mount, width):
    page = mount(width=width, public_share=True)
    risk = page.locator('[data-field-id="3"]')
    assert risk.locator("thead").is_visible()
    assert not risk.locator(".cf-matrix__column").first.is_visible()
    wrap = risk.locator(".cf-matrix-wrap")
    assert wrap.evaluate("(element) => element.scrollWidth <= element.clientWidth")
    rows = risk.locator("tbody tr")
    first, last = rows.nth(0).bounding_box(), rows.nth(4).bounding_box()
    assert last["y"] > first["y"] and abs(last["x"] - first["x"]) < 1
    risk.get_by_role("radio", name="Rare: Catastrophic").click()
    assert risk.get_by_role("radio", name="Rare: Catastrophic").is_checked()
    assert risk.locator("label.cf-matrix__cell").first.bounding_box()["height"] >= 44


def test_matrix_keeps_table_on_wide_screens(mount):
    page = mount(width=1280, public_share=True)
    risk = page.locator('[data-field-id="3"]')
    assert risk.locator("thead").is_visible()
    assert not risk.locator(".cf-matrix__column").first.is_visible()
