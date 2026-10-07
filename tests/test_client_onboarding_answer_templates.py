"""Saved custom answers remain readable in onboarding and company reviews."""

from pathlib import Path
import re

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"


def _environment(*, company_review_only: bool = False) -> Environment:
    overrides = {"base.html": "{% block content %}{% endblock %}"}
    if company_review_only:
        # Render the actual review panels without unrelated company settings.
        source = (TEMPLATE_ROOT / "admin/company_edit.html").read_text()
        overrides["admin/company_edit.html"] = (
            source.split('<section class="card card--panel ce-section" id="ce-general"', 1)[0]
            + "</div></div></div>{% endblock %}"
        )
    env = Environment(
        loader=ChoiceLoader([DictLoader(overrides), FileSystemLoader(TEMPLATE_ROOT)]),
        autoescape=True,
    )
    env.globals["feature_pack_available"] = lambda _key: False
    return env


def _answer(label: str, section: str, value: str) -> dict:
    return {
        "id": 1,
        "label": label,
        "section": section,
        "field_type": "long_text",
        "value": "internal raw value",
        "display_value": value,
    }


def _submission() -> dict:
    return {
        "client_name": "Acme",
        "phone": "07 3000 0000",
        "email": "hello@example.com",
        "billing_contact": {"first_name": "Jo", "last_name": "Smith", "email": "jo@example.com"},
        "custom_answers": [
            _answer("Business question", "business", "Business answer"),
            _answer("Billing question", "billing", "Billing answer"),
            _answer("Locations question", "sites", "Locations answer"),
            _answer("Final question", "review", "Final answer"),
        ],
        "sites": [{
            "name": "Head office",
            "street": "1 Main Street",
            "timezone": "Australia/Brisbane",
            "weekly_hours": {},
            "custom_answers": [_answer("Access instructions", "site", "Call reception\nUse side door")],
        }],
    }


def test_admin_submission_groups_saved_answers_with_their_sections_and_sites():
    html = _environment().get_template("admin/client_onboarding_detail.html").render(
        onboarding={"effective_status": "approved", "status": "approved"},
        submission=_submission(),
        weekdays=[],
    )
    sections = {
        match.group(1): match.group(2)
        for match in re.finditer(r'<section[^>]+aria-labelledby="([^"]+)"[^>]*>(.*?)</section>', html, re.S)
    }
    assert "Business question" in sections["cof-detail-business"]
    assert "Billing question" in sections["cof-detail-billing"]
    assert "Locations answer" in sections["cof-detail-custom-questions"]
    assert "Final answer" in sections["cof-detail-custom-questions"]
    assert "Call reception\nUse side door" in sections["cof-detail-sites"]
    assert "internal raw value" not in html


def test_saved_answer_display_escapes_content_and_preserves_empty_and_numeric_values():
    env = _environment()
    template = env.from_string(
        '{% from "admin/_client_onboarding_answers.html" import answer_list %}'
        "{{ answer_list(answers) }}"
    )
    answers = [
        _answer("<script>question</script>", "review", "<img src=x onerror=alert(1)>\nSecond line"),
        _answer("Optional", "review", ""),
        _answer("Number", "review", 0),
    ]
    html = template.render(answers=answers)
    assert "&lt;script&gt;question&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;\nSecond line" in html
    assert "white-space: pre-wrap" in html
    assert "Not answered" in html
    assert ">0</dd>" in html
    assert "internal raw value" not in html


def test_company_review_retains_custom_answers_after_approval_for_super_admins():
    template = _environment(company_review_only=True).get_template("admin/company_edit.html")
    context = {
        "company": {"id": 1, "name": "Acme", "pending_approval": False},
        "form_data": {},
        "module_enabled": {},
        "client_onboarding": {"id": 2, "submission": _submission()},
        "is_super_admin": True,
    }
    html = template.render(**context)
    assert "Onboarding custom questions" in html
    assert 'href="#ce-onboarding-answers" data-ce-link="onboarding-answers"' in html
    assert 'id="ce-onboarding-answers" data-company-edit-section="onboarding-answers"' in html
    assert "Business answer" in html
    assert "Head office" in html
    assert "Call reception\nUse side door" in html
    assert "Pending approval" not in html
    assert "Onboarding custom questions" not in template.render(**{**context, "is_super_admin": False})


def test_old_submissions_without_custom_answers_do_not_show_empty_panels():
    submission = _submission()
    submission.pop("custom_answers")
    submission["sites"][0].pop("custom_answers")
    html = _environment().get_template("admin/client_onboarding_detail.html").render(
        onboarding={"effective_status": "approved", "status": "approved"},
        submission=submission,
        weekdays=[],
    )
    assert "cof-detail-custom-questions" not in html
