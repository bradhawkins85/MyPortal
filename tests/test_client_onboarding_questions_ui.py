"""Browser checks for custom questions in the public onboarding journey."""

from pathlib import Path
import os
import re
import shutil

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

playwright_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


def _question(question_id, field_type, section="business", required=False, **values):
    return {
        "id": question_id,
        "label": f"Question {question_id}",
        "field_type": field_type,
        "section": section,
        "help_text": "Help with this question.",
        "required": required,
        "options": ["First option", "Second option"],
        "display_order": question_id,
        **values,
    }


def _form(answers=None, site_answers=None):
    hours = [
        {"key": key, "label": label, "open": False, "start": "", "end": "", "start2": "", "end2": ""}
        for key, label in zip(
            ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )
    ]
    return {
        "client_name": "Acme",
        "greeting_name": "Jo",
        "values": {"company_phone": "07 3000 0000", "company_email": "hello@acme.com.au"},
        "custom_answers": answers or {},
        "sites": [{
            "index": "0",
            "values": {"name": "Head office", "street": "1 Main Street", "contact_first_name": "Jo", "contact_last_name": "Smith", "contact_email": "jo@acme.com.au", "timezone": "Australia/Brisbane"},
            "hours": hours,
            "custom_answers": site_answers or {},
        }],
        "billing_same_as_primary": True,
        "confirm_details": False,
    }


@pytest.fixture
def onboarding_page():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run onboarding browser checks")
    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("System Chromium is required for onboarding browser checks")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=["--disable-crash-reporter", "--disable-breakpad"])
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        assert not errors, errors
        browser.close()


def _mount(page, questions=(), answers=None, site_answers=None, errors=()):
    sections = {}
    for question in questions:
        sections.setdefault(question["section"], []).append(question)
    state = _form(answers, site_answers)
    environment = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=select_autoescape())
    rendered = environment.get_template("onboarding/client_form.html").render(
        state="open", app_name="MyPortal", form=state, token="test", errors=errors,
        question_sections=sections, timezones=["Australia/Brisbane"], default_timezone="Australia/Brisbane",
        max_sites=20, blank_hours=state["sites"][0]["hours"], static_url=lambda path: path,
    )
    rendered = re.sub(r'<link[^>]*>|<script\b[^>]*>.*?</script>', "", rendered, flags=re.S)
    page.set_content(rendered)
    page.add_style_tag(content=(ROOT / "app/static/css/app.css").read_text())
    page.add_style_tag(content=(ROOT / "app/static/css/client_onboarding.css").read_text())
    page.add_script_tag(content=(ROOT / "app/static/js/client_onboarding.js").read_text())


def _continue(page, step):
    page.locator(f'[data-cof-step="{step}"] [data-cof-next]').click()


def test_native_controls_restore_answers_and_accessible_descriptions(onboarding_page):
    page = onboarding_page
    types = ["short_text", "long_text", "email", "phone", "date", "dropdown", "multi_select", "checkbox", "radio", "number", "url", "time", "datetime_local"]
    values = ["Short answer", "Long\nanswer", "ops@acme.com.au", "+61 7 3000 0000", "2026-10-07", "Second option", ["First option", "Second option"], True, "Second option", "12.5", "https://acme.com.au", "09:30:15", "2026-10-07T09:30:15"]
    _mount(page, [_question(index + 1, kind) for index, kind in enumerate(types)], dict(zip(map(str, range(1, 14)), values)))
    for index, kind in enumerate(types, 1):
        controls = page.locator(f'[data-cof-custom-question="{index}"] [data-cof-custom-control]')
        if kind in ("checkbox", "multi_select", "radio"):
            assert controls.count() >= 1
            assert controls.evaluate_all("controls => controls.some(input => input.checked)")
        else:
            assert controls.input_value() == values[index - 1]
        assert controls.first.evaluate("input => input.labels.length > 0")
        for description_id in controls.first.get_attribute("aria-describedby").split():
            assert page.locator(f'#{description_id}').count() == 1
    assert page.locator("#custom-1").get_attribute("maxlength") == "1000"
    assert page.locator("#custom-2").get_attribute("maxlength") == "10000"
    assert page.locator("#custom-11").get_attribute("maxlength") == "2048"
    assert page.locator("#custom-12").get_attribute("step") == "1"
    assert page.locator("#custom-13").get_attribute("step") == "1"


def test_required_groups_sections_and_billing_same_are_validated(onboarding_page):
    page = onboarding_page
    _mount(page, [
        _question(1, "multi_select", required=True), _question(2, "radio", required=True),
        _question(3, "dropdown", "sites", required=True), _question(4, "checkbox", "billing", required=True),
        _question(5, "long_text", "review", required=True),
    ])
    _continue(page, "business")
    assert page.locator('[data-cof-step="business"]').get_attribute("class").endswith("is-current")
    assert page.locator("#custom-1-error").inner_text() == "Select at least one option."
    page.locator("#custom-1-2").check()
    assert page.locator("#custom-1-error").is_hidden()
    page.locator("#custom-2-1").check()
    _continue(page, "business")
    _continue(page, "sites")
    assert page.locator("#custom-3-error").inner_text() == "Select an option."
    page.locator("#custom-3").select_option("First option")
    _continue(page, "sites")
    assert page.locator('[data-cof-billing-fields]').is_hidden()
    assert page.locator("#custom-4").is_enabled()
    _continue(page, "billing")
    assert page.locator("#custom-4-error").inner_text() == "Tick the box to answer this question."
    page.locator("#custom-4").check()
    _continue(page, "billing")
    page.locator('[data-cof-field="confirm_details"]').check()
    page.locator('[data-cof-submit]').click()
    assert page.locator("#custom-5-error").inner_text() == "Answer this question."
    assert page.locator('[data-cof-submit]').is_enabled()


def test_site_questions_validate_tabs_and_cancel_preserves_saved_values(onboarding_page):
    page = onboarding_page
    _mount(page, [
        _question(1, "multi_select", "site_address", required=True),
        _question(2, "email", "site_contact", required=True),
        _question(3, "long_text", "site_hours", required=True),
    ])
    _continue(page, "business")
    _continue(page, "sites")
    modal = page.locator('[data-cof-site-modal="0"]')
    assert modal.is_visible()
    modal.locator('[data-cof-site-save]').click()
    assert modal.locator('[data-cof-tab="address"]').get_attribute("aria-selected") == "true"
    modal.locator("#site-0-custom-1-2").check()
    modal.locator('[data-cof-site-save]').click()
    assert modal.locator('[data-cof-tab="contact"]').get_attribute("aria-selected") == "true"
    modal.locator("#site-0-custom-2").fill("wrong-email")
    modal.locator('[data-cof-site-save]').click()
    assert modal.locator("#site-0-custom-2-error").inner_text().startswith("Enter a valid email")
    modal.locator("#site-0-custom-2").fill("site@acme.com.au")
    modal.locator('[data-cof-site-save]').click()
    assert modal.locator('[data-cof-tab="hours"]').get_attribute("aria-selected") == "true"
    modal.locator("#site-0-custom-3").fill("Access via side door")
    modal.locator('[data-cof-site-save]').click()
    assert modal.is_hidden()
    page.locator('[data-cof-site-edit="0"]').first.click()
    modal.locator("#site-0-custom-1-2").uncheck()
    modal.locator("#site-0-custom-1-1").check()
    modal.locator('[data-cof-tab="contact"]').click()
    modal.locator("#site-0-custom-2").fill("changed@acme.com.au")
    modal.locator('[data-cof-tab="hours"]').click()
    modal.locator("#site-0-custom-3").fill("Changed answer")
    modal.locator('[data-cof-modal-cancel]').last.click()
    assert modal.locator("#site-0-custom-1-2").is_checked()
    assert not modal.locator("#site-0-custom-1-1").is_checked()
    assert modal.locator("#site-0-custom-2").input_value() == "site@acme.com.au"
    assert modal.locator("#site-0-custom-3").input_value() == "Access via side door"
    page.locator('[data-cof-site-add]').click()
    new_modal = page.locator('[data-cof-site-modal="1"]')
    assert new_modal.locator('[data-cof-custom-question]').count() == 3
    assert not new_modal.locator("#site-1-custom-1-2").is_checked()
    new_modal.locator('[data-cof-modal-cancel]').last.click()
    assert page.locator('[data-cof-site-modal]').count() == 1


def test_review_and_form_data_include_hidden_site_answers(onboarding_page):
    page = onboarding_page
    _mount(page, [
        _question(1, "short_text"), _question(2, "multi_select", "sites"),
        _question(3, "number", "billing"), _question(4, "radio", "site_contact"),
        _question(5, "checkbox", "site_hours"),
    ], {"1": "Business answer", "2": ["First option", "Second option"], "3": "24"}, {"4": "Second option", "5": True})
    _continue(page, "business")
    _continue(page, "sites")
    _continue(page, "billing")
    review = page.locator('[data-cof-review]')
    assert review.get_by_text("Business answer", exact=True).count() == 1
    assert review.get_by_text("First option, Second option", exact=True).count() == 1
    assert review.get_by_text("24", exact=True).count() == 1
    assert review.get_by_text("Second option", exact=True).count() == 1
    assert review.get_by_text("Yes", exact=True).count() == 1
    submitted = page.evaluate("() => Array.from(new FormData(document.querySelector('[data-cof-form]')).entries())")
    assert ["site-0-custom-4", "Second option"] in submitted
    assert ["site-0-custom-5", "1"] in submitted
    assert ["custom-2", "First option"] in submitted
    assert ["custom-2", "Second option"] in submitted
    assert ["custom-3", "24"] in submitted


def test_forward_progress_and_submit_recheck_modified_earlier_answers(onboarding_page):
    page = onboarding_page
    _mount(page, [_question(1, "short_text", required=True)], {"1": "Saved answer"})
    _continue(page, "business")
    _continue(page, "sites")
    _continue(page, "billing")
    page.locator('[data-cof-goto="business"]').click()
    page.locator("#custom-1").fill("")
    page.locator('[data-cof-goto="review"]').click()
    assert page.locator("#custom-1-error").is_visible()
    assert page.locator('[data-cof-step="business"]').is_visible()
    page.locator("#custom-1").fill("Updated answer")
    page.locator('[data-cof-goto="review"]').click()
    page.locator('[data-cof-field="confirm_details"]').check()
    page.locator("#custom-1").evaluate("input => input.value = ''")
    page.locator('[data-cof-submit]').click()
    assert page.locator('[data-cof-step="business"]').is_visible()
    assert page.locator("#custom-1").evaluate("input => document.activeElement === input")
    assert page.locator('[data-cof-submit]').is_enabled()


@pytest.mark.parametrize("field_type, answer", [("url", "ftp://acme.com.au"), ("phone", "abc"), ("number", "1e101")])
def test_optional_answers_still_validate_field_formats(onboarding_page, field_type, answer):
    page = onboarding_page
    _mount(page, [_question(1, field_type)])
    page.locator("#custom-1").fill(answer)
    _continue(page, "business")
    assert page.locator("#custom-1-error").is_visible()
    assert page.locator('[data-cof-step="business"]').is_visible()


def test_review_server_errors_select_review_and_custom_controls_fit_mobile(onboarding_page):
    page = onboarding_page
    _mount(page, [_question(1, "long_text", "review", required=True)], errors=["Review: Question 1: answer this question."])
    assert page.locator('[data-cof-step="review"]').is_visible()
    for width in [320, 768, 1024, 1440]:
        page.set_viewport_size({"width": width, "height": 900})
        bounds = page.locator("#custom-1").bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
