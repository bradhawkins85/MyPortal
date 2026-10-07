"""Admin question editors preserve entered settings and protect all mutations."""

from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"
FIELD_TYPES = {key: key.replace("_", " ").title() for key in (
    "short_text", "long_text", "email", "phone", "date", "dropdown",
    "multi_select", "checkbox", "radio", "number", "url", "time", "datetime_local",
)}
SECTIONS = {
    "business": "Your business", "sites": "Sites", "billing": "Billing", "review": "Review",
    "site_address": "address", "site_contact": "primary contact", "site_hours": "opening hours",
}


def _render(**overrides):
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": "{% block content %}{% endblock %}"}),
            FileSystemLoader(TEMPLATE_ROOT),
        ]),
        autoescape=True,
    )
    context = {
        "field_types": FIELD_TYPES, "question_sections": SECTIONS, "questions": [],
        "question_form": {}, "question_errors": [], "editing_question_id": None,
        "csrf_token": "test-csrf", "max_questions": 100,
    }
    return env.get_template("admin/client_onboarding_questions.html").render(context | overrides)


def _question(**overrides):
    return {
        "id": 8, "label": "Which services?", "field_type": "multi_select", "section": "business",
        "help_text": "Choose all that apply.", "required": True,
        "options": ["Support", "Internet"], "display_order": 10,
    } | overrides


class _Tags(HTMLParser):
    def __init__(self, source, tag):
        super().__init__()
        self.tag = tag
        self.attributes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == self.tag:
            self.attributes.append(dict(attrs))


def _tags(source, tag):
    return _Tags(source, tag).attributes


def _forms(source):
    return re.findall(r"<form\b[^>]*>.*?</form>", source, re.S)


def _input(form, name):
    return next(attrs for attrs in _tags(form, "input") if attrs.get("name") == name)


def _selected(form, name):
    select = next(source for source in re.findall(r"<select\b[^>]*>.*?</select>", form, re.S)
                  if _tags(source, "select")[0].get("name") == name)
    return [attrs["value"] for attrs in _tags(select, "option") if "selected" in attrs]


def test_add_edit_and_delete_forms_each_include_csrf_and_correct_question_identity():
    forms = _forms(_render(questions=[_question()]))
    assert len(forms) == 3
    for form in forms:
        assert _tags(form, "form")[0]["method"] == "post"
        assert _input(form, "_csrf")["value"] == "test-csrf"
    add, edit, delete = forms
    assert _tags(add, "form")[0]["action"] == "/admin/client-onboarding/questions"
    assert not any(attrs.get("name") == "question_id" for attrs in _tags(add, "input"))
    assert _tags(edit, "form")[0]["action"] == "/admin/client-onboarding/questions"
    assert _input(edit, "question_id")["value"] == "8"
    assert _tags(delete, "form")[0]["action"] == "/admin/client-onboarding/questions/8/delete"


def test_failed_edit_reopens_its_editor_and_preserves_entered_settings():
    entered = _question(label="Updated question", field_type="radio", section="site_hours",
                        help_text="New help", required=False, options="Support\nSupport", display_order="0")
    html = _render(questions=[_question(), _question(id=9)], question_form=entered,
                   question_errors=["Enter distinct options."], editing_question_id=8)
    details = _tags(html, "details")
    assert [attrs["id"] for attrs in details if "open" in attrs] == ["coq-question-8"]
    assert "Enter distinct options." in html
    edit = next(form for form in _forms(html)
                if any(attrs.get("name") == "question_id" and attrs.get("value") == "8"
                       for attrs in _tags(form, "input")))
    assert _input(edit, "label")["value"] == "Updated question"
    assert _input(edit, "display_order")["value"] == "0"
    assert "checked" not in _input(edit, "required")
    assert _selected(edit, "field_type") == ["radio"]
    assert _selected(edit, "section") == ["site_hours"]
    assert ">Support\nSupport</textarea>" in edit
    assert ">New help</textarea>" in edit


def test_question_limit_preserves_failed_add_fields_but_disables_adding():
    questions = [_question(id=question_id) for question_id in range(1, 101)]
    html = _render(questions=questions, question_errors=["Add no more than 100 custom questions."],
                   question_form=_question(label="Unsaved question", field_type="email", section="billing",
                                           help_text="Unsaved help", options="", display_order="3"))
    add = _forms(html)[0]
    assert _input(add, "label")["value"] == "Unsaved question"
    assert _input(add, "display_order")["value"] == "3"
    assert _selected(add, "field_type") == ["email"]
    assert _selected(add, "section") == ["billing"]
    assert ">Unsaved help</textarea>" in add
    assert "disabled" in _tags(add, "button")[0]
    assert "Remove a question before adding another." in html


def test_question_content_is_escaped_and_saved_type_section_and_options_remain_selected():
    question = _question(label='<script>alert("question")</script>',
                         help_text="<img src=x onerror=alert(1)>",
                         options=["</textarea><script>alert(1)</script>", "A & B"],
                         field_type="dropdown", section="site_contact")
    html = _render(questions=[question])
    edit = _forms(html)[1]
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt;\nA &amp; B</textarea>" in edit
    assert _input(edit, "label")["value"] == question["label"]
    assert _selected(edit, "field_type") == ["dropdown"]
    assert _selected(edit, "section") == ["site_contact"]
    assert "checked" in _input(edit, "required")
    assert "Each site: primary contact" in unescape(html)
