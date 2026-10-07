"""Optional end-to-end checks for independent site answers and no-JS forms.

Run with RUN_BROWSER_TESTS=1 and a system Chromium installation, or set
PLAYWRIGHT_CHROMIUM_EXECUTABLE to the desired browser executable. All browser
requests are intercepted; these tests need no application server or network.
"""

import os
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _question(question_id, field_type, section, *, required=False, **overrides):
    return {
        "id": question_id, "label": f"Question {question_id}",
        "field_type": field_type, "section": section, "required": required,
        "help_text": "Use the details for this location.",
        "options": ["Office", "Warehouse"], "display_order": question_id,
        **overrides,
    }


def _state(answers=None, site_answers=None):
    hours = [
        {"key": key, "label": label, "open": False,
         "start": "", "end": "", "start2": "", "end2": ""}
        for key, label in zip(
            ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )
    ]
    return {
        "client_name": "Acme", "greeting_name": "Jo",
        "values": {"company_phone": "07 3000 0000", "company_email": "hello@acme.com.au"},
        "custom_answers": answers or {}, "billing_same_as_primary": True,
        "confirm_details": False,
        "sites": [{
            "index": "0", "values": {
                "name": "Head office", "street": "1 Main Street",
                "contact_first_name": "Jo", "contact_last_name": "Smith",
                "contact_email": "jo@acme.com.au", "timezone": "Australia/Brisbane",
            }, "hours": hours, "custom_answers": site_answers or {},
        }],
    }


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
    script_errors = []

    def create(questions, *, answers=None, site_answers=None, java_script_enabled=True):
        sections = {}
        for question in questions:
            sections.setdefault(question["section"], []).append(question)
        state = _state(answers, site_answers)
        environment = Environment(
            loader=FileSystemLoader(ROOT / "app/templates"), autoescape=select_autoescape(),
        )
        html = environment.get_template("onboarding/client_form.html").render(
            state="open", app_name="MyPortal", form=state, token="browser-test", errors=[],
            question_sections=sections, timezones=["Australia/Brisbane"],
            default_timezone="Australia/Brisbane", max_sites=20,
            blank_hours=state["sites"][0]["hours"], static_url=lambda path: path,
        )
        context = browser.new_context(java_script_enabled=java_script_enabled)
        contexts.append(context)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        page.on("console", lambda message: script_errors.append(message.text) if message.type == "error" else None)
        submitted = {}

        def intercept(route):
            request = route.request
            path = urlsplit(request.url).path
            if request.method == "POST" and path == "/onboarding/browser-test":
                submitted.update(parse_qs(request.post_data or "", keep_blank_values=True))
                route.fulfill(status=200, content_type="text/html", body="<h1>Received</h1>")
            elif path == "/onboarding/browser-test":
                route.fulfill(status=200, content_type="text/html", body=html)
            elif path.startswith("/static/"):
                asset = ROOT / "app" / path.lstrip("/")
                if asset.is_file():
                    mime = "text/css" if asset.suffix == ".css" else "text/javascript" if asset.suffix == ".js" else "image/svg+xml"
                    route.fulfill(status=200, content_type=mime, body=asset.read_bytes())
                else:
                    route.fulfill(status=204, body="")
            else:
                route.abort()

        context.route("**/*", intercept)
        page.goto("http://onboarding.test/onboarding/browser-test")
        if java_script_enabled:
            page.locator(".cof-form.is-enhanced").wait_for()
        return page, submitted

    yield create
    for context in contexts:
        context.close()
    assert not script_errors, script_errors


def _continue(page, step):
    page.locator(f'[data-cof-step="{step}"] [data-cof-next]').click()


def _assert_no_horizontal_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), page.evaluate(
        "({width: window.innerWidth, content: document.documentElement.scrollWidth})"
    )


def test_multiple_sites_keep_answers_separate_through_cancel_remove_and_submit(mount):
    questions = [
        _question(1, "short_text", "business", required=True),
        _question(2, "radio", "site_address", required=True),
        _question(3, "multi_select", "site_contact"),
        _question(4, "short_text", "site_hours", required=True),
        _question(5, "date", "billing", required=True),
        _question(6, "dropdown", "sites"),
        _question(7, "checkbox", "review", required=True),
        _question(8, "long_text", "site_address", label="Access instructions " + "x" * 180),
    ]
    page, submitted = mount(
        questions, answers={"1": "Managed support", "5": "2026-10-07", "6": "Office"},
        site_answers={"2": "Office", "3": ["Office"], "4": "Reception"},
    )
    page.set_viewport_size({"width": 320, "height": 900})
    _assert_no_horizontal_overflow(page)
    _continue(page, "business")
    _assert_no_horizontal_overflow(page)
    page.locator('[data-cof-site-add]').click()
    second = page.locator('[data-cof-site-modal="1"]')
    assert second.locator('[data-cof-custom-question]').count() == 4
    assert not second.locator('[data-cof-custom-control]:checked').count()
    assert second.locator("#site-1-custom-4").input_value() == ""
    _assert_no_horizontal_overflow(page)
    second.locator('[data-cof-field="name"]').fill("Warehouse")
    second.locator('[data-cof-field="street"]').fill("2 Warehouse Road")
    second.locator('[data-cof-tab="contact"]').click()
    second.locator('[data-cof-field="contact_first_name"]').fill("Sam")
    second.locator('[data-cof-field="contact_last_name"]').fill("Jones")
    second.locator('[data-cof-field="contact_email"]').fill("sam@acme.com.au")
    second.locator('[data-cof-tab="address"]').click()
    second.locator('[data-cof-site-save]').click()
    assert second.locator("#site-1-custom-2-error").is_visible()
    assert second.locator('[data-cof-tab="address"]').get_attribute("aria-selected") == "true"
    second.locator("#site-1-custom-2-2").check()
    second.locator("#site-1-custom-8").fill("Use the loading dock")
    second.locator('[data-cof-tab="contact"]').click()
    second.locator("#site-1-custom-3-1").check()
    second.locator("#site-1-custom-3-2").check()
    second.locator('[data-cof-site-save]').click()
    assert second.locator('[data-cof-tab="hours"]').get_attribute("aria-selected") == "true"
    second.locator("#site-1-custom-4").fill("Loading dock")
    second.locator('[data-cof-site-save]').click()
    assert second.is_hidden()

    page.locator('[data-cof-site-edit="1"]').first.click()
    second.locator("#site-1-custom-2-1").check()
    second.locator('[data-cof-tab="contact"]').click()
    second.locator("#site-1-custom-3-2").uncheck()
    second.locator('[data-cof-modal-cancel]').last.click()
    assert second.locator("#site-1-custom-2-2").is_checked()
    assert second.locator("#site-1-custom-3-2").is_checked()
    assert page.locator("#site-0-custom-2-1").is_checked()
    assert not page.locator("#site-0-custom-3-2").is_checked()

    # Removing and re-adding a site never keeps a removed site's controls/answers.
    page.locator('[data-cof-site-add]').click()
    page.locator('[data-cof-site-modal="2"] #site-2-custom-8').fill("Discarded site answer")
    page.locator('[data-cof-site-modal="2"] [data-cof-site-remove]').click()
    assert not page.locator('[data-cof-site-modal="2"]').count()
    page.locator('[data-cof-site-add]').click()
    assert page.locator("#site-2-custom-8").input_value() == ""
    page.locator('[data-cof-site-modal="2"] [data-cof-modal-cancel]').last.click()
    assert page.locator('[data-cof-site-modal]').count() == 2
    assert page.locator('[data-cof-site-role]').all_text_contents() == ["Main site", "Site 2"]
    assert page.evaluate("() => { const ids=[...document.querySelectorAll('[id]')].map(el=>el.id); return ids.length===new Set(ids).size; }")

    _continue(page, "sites")
    _assert_no_horizontal_overflow(page)
    _continue(page, "billing")
    blocks = page.locator(".cof-review__site")
    assert blocks.count() == 2
    assert "Head office (main site)" in blocks.nth(0).inner_text()
    assert "Reception" in blocks.nth(0).inner_text()
    assert "Loading dock" not in blocks.nth(0).inner_text()
    assert "Warehouse" in blocks.nth(1).inner_text()
    assert "Loading dock" in blocks.nth(1).inner_text()
    assert "Office, Warehouse" in blocks.nth(1).inner_text()
    for width in [320, 1024]:
        page.set_viewport_size({"width": width, "height": 900})
        _assert_no_horizontal_overflow(page)
    page.locator("#custom-7").check()
    page.locator('[data-cof-field="confirm_details"]').check()
    page.locator('[data-cof-submit]').click()
    page.get_by_role("heading", name="Received").wait_for()
    assert submitted["site_index"] == ["0", "1"]
    assert submitted["custom-1"] == ["Managed support"]
    assert submitted["custom-5"] == ["2026-10-07"]
    assert submitted["custom-7"] == ["1"]
    assert submitted["site-0-custom-2"] == ["Office"]
    assert submitted["site-1-custom-2"] == ["Warehouse"]
    assert submitted["site-0-custom-3"] == ["Office"]
    assert submitted["site-1-custom-3"] == ["Office", "Warehouse"]
    assert submitted["site-0-custom-4"] == ["Reception"]
    assert submitted["site-1-custom-4"] == ["Loading dock"]
    assert submitted["site-1-custom-8"] == ["Use the loading dock"]
    assert not any(key.startswith("site-2-") for key in submitted)


def test_no_javascript_custom_questions_remain_visible_and_submit_natively(mount):
    page, submitted = mount(
        [
            _question(1, "short_text", "business", required=True),
            _question(2, "multi_select", "site_contact", required=True),
            _question(3, "long_text", "site_hours", required=True),
            _question(4, "email", "billing", required=True),
            _question(5, "radio", "review", required=True),
        ], java_script_enabled=False,
    )
    for width in [320, 1024]:
        page.set_viewport_size({"width": width, "height": 900})
        for field in ["#custom-1", "#site-0-custom-2-1", "#site-0-custom-3", "#custom-4", "#custom-5-1"]:
            assert page.locator(field).is_visible()
        _assert_no_horizontal_overflow(page)
    page.locator("#custom-1").fill("No JavaScript business answer")
    page.locator("#site-0-custom-2-1").check()
    page.locator("#site-0-custom-2-2").check()
    page.locator("#site-0-custom-3").fill("No JavaScript hours answer")
    page.locator("#custom-4").fill("accounts@acme.com.au")
    page.locator("#custom-5-2").check()
    page.locator('[data-cof-field="confirm_details"]').check()
    page.get_by_role("button", name="Send my details").click()
    page.get_by_role("heading", name="Received").wait_for()
    assert submitted["custom-1"] == ["No JavaScript business answer"]
    assert submitted["site-0-custom-2"] == ["Office", "Warehouse"]
    assert submitted["site-0-custom-3"] == ["No JavaScript hours answer"]
    assert submitted["custom-4"] == ["accounts@acme.com.au"]
    assert submitted["custom-5"] == ["Warehouse"]
