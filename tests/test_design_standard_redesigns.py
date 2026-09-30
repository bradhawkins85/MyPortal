"""Rendering checks for pages redesigned to the gold-standard UI patterns."""
from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2

TEMPLATES = Path("app/templates")


def _env() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)


def _section(template: str, start: str, end: str) -> str:
    source = (TEMPLATES / template).read_text()
    return source[source.index(start) : source.index(end)]


def _json_block(html: str, element_id: str) -> dict:
    match = re.search(rf'id="{element_id}">(.*?)</script>', html, re.S)
    assert match, element_id
    return json.loads(match.group(1))


def test_staff_intake_fields_render_as_card_list_with_modal_editor():
    snippet = _section(
        "admin/company_edit.html", "{% set sif_type_labels", "{% set scf_type_labels"
    )
    fields = [
        {"key": "first_name", "label": "First name", "type": "text", "visible": True,
         "required": True, "sort_order": 1, "options": []},
        {"key": "department", "label": "Department", "type": "select", "visible": True,
         "required": False, "sort_order": 5, "options": [{"value": "s", "label": "Sales"}]},
        {"key": "shirt", "label": "Shirt", "type": "multiselect", "visible": False,
         "required": False, "sort_order": 9, "options": []},
    ]
    html = _env().from_string(snippet).render(
        staff_field_config=fields, company={"id": 3}, csrf_token="t"
    )

    assert 'data-sif-edit="department"' in html
    assert 'id="staff-intake-field-modal"' in html
    assert "sif-chip--hidden" in html and "No options yet" in html
    # Raw "value:Label" strings only survive in the no-JavaScript fallback.
    assert html.count('name="field_department_options"') == 1
    assert html.index("<noscript>") < html.index('name="field_department_options"')
    data = _json_block(html, "staff-intake-field-editor-data")
    assert data["coreKeys"] == ["first_name", "last_name"]
    assert data["fields"][1]["options"] == [{"value": "s", "label": "Sales"}]


def test_tray_ticket_questions_render_grouped_list_and_editor():
    questions = [
        {"id": 1, "scope": "global", "company_id": None, "label": "Which site?", "placeholder": "",
         "field_type": "select", "is_required": True, "is_active": True, "sort_order": 10,
         "options": ["Sydney"], "conditions": []},
        {"id": 3, "scope": "company", "company_id": 5, "label": "Room", "placeholder": "",
         "field_type": "text", "is_required": False, "is_active": False, "sort_order": 5,
         "options": [], "conditions": [
             {"parent_question_id": 1, "operator": "equals", "expected_value": "Sydney"}
         ]},
    ]
    html = _env().from_string(
        '{% include "admin/tray/_ticket_question_editor.html" %}'
    ).render(
        tq_questions=questions,
        tq_parents=questions,
        tq_base_url="/admin/tray/ticket-questions",
        tq_companies=[{"id": 5, "name": "Acme"}],
        tq_company=None,
        csrf_token="t",
    )

    assert html.index("Global — every company") < html.index("Company — Acme")
    assert "Shown when Which site? is “Sydney”" in html
    # Edit links keep working without JavaScript and open the modal with it.
    assert 'href="/admin/tray/ticket-questions/3/edit" data-tq-edit="3"' in html
    assert 'id="tq-modal"' in html
    data = _json_block(html, "tq-editor-data")
    assert data["baseUrl"] == "/admin/tray/ticket-questions"
    assert data["questions"][1]["conditions"][0]["expected_value"] == "Sydney"


def test_tray_ticket_questions_empty_state_offers_first_question():
    html = _env().from_string(
        '{% include "admin/tray/_ticket_question_editor.html" %}'
    ).render(
        tq_questions=[],
        tq_parents=[],
        tq_base_url="/admin/companies/2/tray/ticket-questions",
        tq_companies=None,
        tq_company={"id": 2, "name": "Globex"},
        csrf_token="t",
    )

    assert "No questions yet" in html
    assert "+ Add your first question" in html
    assert 'name="company_id" value="2"' in html


def test_scheduled_ticket_payload_macro_keeps_json_field_name():
    html = _env().from_string(
        '{% from "macros/scheduled_ticket_payload.html" import scheduled_ticket_payload %}'
        "{{ scheduled_ticket_payload('bulk-task', 'hidden', 'Copied to each company.') }}"
    ).render()

    assert 'id="bulk-task-json-payload-field" hidden data-ticket-payload' in html
    assert 'name="jsonPayload"' in html
    assert 'data-stp-field="subject"' in html
    assert "Copied to each company." in html


def test_scheduled_task_pages_use_structured_ticket_payload():
    for template in (
        "admin/scheduled_tasks.html",
        "admin/automation.html",
        "admin/company_edit.html",
    ):
        source = (TEMPLATES / template).read_text()
        assert "JSON Payload</label>" not in source, template
        assert "scheduled_ticket_payload('task'" in source, template
        assert "scheduled_ticket_payload.js" in source, template


def test_mail_filter_macro_renders_builder_around_filter_json():
    stored = {"all": [{"field": "from.domain", "equals": "example.com"}]}
    html = _env().from_string(
        '{% from "macros/mail_filter.html" import mail_filter_field %}'
        "{{ mail_filter_field(value) }}"
    ).render(value=stored)

    assert "data-mail-filter" in html
    assert "Only emails that match rules" in html
    match = re.search(r'<textarea[^>]*name="filterQuery"[^>]*>(.*?)</textarea>', html, re.S)
    assert match
    assert json.loads(match.group(1).replace("&#34;", '"').replace("&quot;", '"')) == stored


def test_mailbox_pages_use_mail_filter_builder():
    for template in ("admin/imap.html", "admin/m365_mail.html"):
        source = (TEMPLATES / template).read_text()
        assert "Message filter (JSON)" not in source, template
        assert "mail_filter_field(editing_account.filter_query if is_editing else none)" in source
        assert "mail_filter_builder.js" in source, template


def test_smtp2go_campaigns_render_list_and_editor():
    source = (TEMPLATES / "admin/smtp2go.html").read_text()
    assert "data-ab-campaigns" in source
    assert 'id="abc-modal"' in source
    assert 'name="abCampaignsRaw"' in source
    assert "smtp2go_campaigns.js" in source


def _page_env() -> jinja2.Environment:
    """Render full pages against a minimal stand-in for base.html."""

    class _Loader(jinja2.FileSystemLoader):
        def get_source(self, environment, template):
            if template == "base.html":
                source = (
                    "{% block header_actions %}{% endblock %}"
                    "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"
                )
                return source, None, lambda: True
            return super().get_source(environment, template)

    env = jinja2.Environment(loader=_Loader(str(TEMPLATES)), autoescape=True)
    env.globals["static_url"] = lambda path: path
    return env


def test_signature_list_renders_cards_with_sandboxed_thumbnails():
    from datetime import date

    templates = [
        {"id": 1, "name": "Standard", "slug": "standard", "description": "", "status": "published",
         "priority": 5, "is_default": True, "schedule_start_on": None, "schedule_end_on": None,
         "html_content": '<p class="x">{{staff.first_name}} "quoted"</p>', "text_content": "x",
         "updated_at": None},
        {"id": 2, "name": "Holidays", "slug": "holidays", "description": "", "status": "published",
         "priority": 9, "is_default": False, "schedule_start_on": date(2026, 12, 1),
         "schedule_end_on": date(2026, 12, 31), "html_content": "", "text_content": "",
         "updated_at": None},
    ]
    html = _page_env().get_template("m365/signatures.html").render(
        templates=templates,
        current_primary=templates[0],
        schedule_timezone="UTC",
        schedule_today=date(2026, 9, 30),
        csrf_token="t",
    )

    assert 'sandbox=""' in html
    # Signature HTML is escaped inside srcdoc so it can't break out of the attribute.
    assert "&lt;p class=&#34;x&#34;&gt;" in html
    assert "In use today" in html
    assert "Scheduled" in html
    assert 'datetime="2026-12-01"' in html
    assert "No plain-text version" in html
    assert "strftime" not in (TEMPLATES / "m365/signatures.html").read_text()


def test_signature_editor_renders_tabs_preview_and_delete_form():
    html = _page_env().get_template("m365/signatures_form.html").render(
        template_record={"id": 4, "status": "published"},
        form_values={"slug": "standard", "name": "Standard", "description": "", "html_content": "<p>Hi</p>",
                     "text_content": "", "priority": "0", "is_default": "", "schedule_start_on": "",
                     "schedule_end_on": ""},
        preview={"html": "<p>Jane</p>", "text": "Jane", "missing_tokens": ["staff.job_title"]},
        preview_staff=[{"id": 7, "label": "Jane", "email": "jane@example.com"}],
        selected_staff_id=7,
        variable_suggestions=["{{staff.first_name}}"],
        schedule_timezone="UTC",
        csrf_token="t",
    )

    assert 'data-sig-tab="schedule"' in html
    assert 'data-sig-unsaved="true"' in html
    assert 'action="/m365/signatures/4/delete" id="sig-delete-form"' in html
    assert 'action="/m365/signatures/4/disable"' in html
    assert 'data-sig-var="{{staff.first_name}}"' in html
    assert "staff.job_title" in html


def test_out_of_office_renders_mailbox_cards_and_editor():
    mailboxes = [
        {"display_name": "Jane Doe", "user_principal_name": "jane@example.com"},
        {"display_name": "Dan Wu", "user_principal_name": "dan@example.com"},
    ]
    states = [
        {"mailbox": "Jane@example.com", "success": True, "error": None, "setting": {
            "status": "scheduled", "externalAudience": "contactsOnly",
            "internalReplyMessage": "<p>On leave</p>", "externalReplyMessage": "Away",
            "scheduledStartDateTime": {"dateTime": "2026-10-01T00:00:00", "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": "2026-10-05T00:00:00", "timeZone": "UTC"},
        }},
        {"mailbox": "dan@example.com", "success": False, "setting": None, "error": "Access denied"},
    ]
    env = _page_env()
    html = env.get_template("m365/out_of_office.html").render(
        mailboxes=mailboxes, states=states, state_error=None, outcomes=[], submitted={},
        can_write=True, csrf_token="t",
    )

    assert 'data-mailbox="jane@example.com"' in html
    assert "Colleagues + external contacts" in html
    assert "Couldn't read: Access denied" in html
    assert 'id="oof-modal"' in html
    assert 'name="mailboxes" value="dan@example.com"' in html
    assert 'data-oof-select-visible' in html
    assert 'data-oof-new' in html
    assert '/static/js/m365_out_of_office.js' in html
    data = _json_block(html, "oof-data")
    assert data["mailboxes"][0]["setting"]["externalAudience"] == "contactsOnly"
    assert data["canWrite"] is True

    read_only = env.get_template("m365/out_of_office.html").render(
        mailboxes=mailboxes, states=states, state_error=None, outcomes=[], submitted={},
        can_write=False, csrf_token="t",
    )
    assert 'id="oof-modal"' not in read_only
    assert "data-oof-select" not in read_only
    assert "read-only access" in read_only


def _auth_env() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.globals.update(
        static_url=lambda path: path,
        feature_pack_available=lambda *args, **kwargs: False,
        sidebar_default_preferences=lambda: {"order": [], "hidden": [], "groups": []},
        deployment_slot=None,
    )
    return env


def test_login_page_keeps_auth_hooks_and_hides_totp_until_needed():
    html = _auth_env().get_template("auth/login.html").render(
        app_name="MyPortal", current_user=None, csrf_token="t", request=None,
        verification_success=False, next_path="/tickets",
    )

    assert 'data-endpoint="/auth/login"' in html
    assert 'data-success-redirect="/tickets"' in html
    assert "data-passkey-login" in html and "data-auth-passkey-section" in html
    assert re.search(r'data-totp-field\s+hidden', html)
    assert "data-auth-toggle-totp" in html
    assert 'name="totp_code"' in html
    assert "data-password-toggle" in html
    assert 'href="/forgot-password"' in html and 'href="/register"' in html
    assert "auth_ui.js" in html and "passkey_utils.js" in html


def test_register_page_variants():
    env = _auth_env()
    common = {"app_name": "MyPortal", "current_user": None, "csrf_token": "t", "request": None}
    html = env.get_template("auth/register.html").render(is_first_user=False, **common)
    assert 'data-endpoint="/auth/register"' in html
    assert 'name="confirm_password"' in html
    assert "data-password-rules" in html
    assert 'name="company_id"' not in html
    assert "Already have an account?" in html

    first = env.get_template("auth/register.html").render(is_first_user=True, **common)
    assert "Create the first account" in first
    assert "First-time setup" in first
    assert '<details class="auth-advanced">' in first and 'name="company_id"' in first
    assert "Already have an account?" not in first


def test_password_help_pages_keep_auth_hooks():
    env = _auth_env()
    common = {"app_name": "MyPortal", "current_user": None, "csrf_token": "t", "request": None}

    forgot = env.get_template("auth/forgot_password.html").render(**common)
    assert 'data-endpoint="/auth/password/forgot"' in forgot
    assert "data-auth-sent-form" in forgot and "data-auth-sent " in forgot
    assert "auth_ui.js" in forgot

    with_token = env.get_template("auth/reset_password.html").render(reset_token="abc", **common)
    assert '<input type="hidden" name="token" value="abc" />' in with_token
    assert 'id="reset-token"' not in with_token
    assert 'name="confirm_password"' in with_token and 'maxlength="128"' in with_token
    assert "data-password-rules" in with_token

    without_token = env.get_template("auth/reset_password.html").render(reset_token="", **common)
    assert 'id="reset-token"' in without_token


def test_totp_enrolment_keeps_script_hooks():
    html = _auth_env().get_template("auth/totp_enrol.html").render(
        app_name="MyPortal", current_user=None, csrf_token="t", request=None,
    )
    for hook in (
        'id="totp-enrol-root"', "data-totp-qr-container", "data-totp-qr-placeholder", "data-totp-qr",
        "data-totp-manual-toggle", "data-totp-manual", 'id="totp-enrol-form"', 'id="totp-name"',
        'id="totp-code"', "data-totp-submit", "data-totp-refresh", "data-logout",
        'id="totp-secret"', 'id="totp-link"', 'data-copy-target="totp-secret"',
    ):
        assert hook in html, hook
    assert html.count('class="totp-step"') == 3


def _issue_tracker_context(**overrides):
    from types import SimpleNamespace

    statuses = [
        {"value": "new", "label": "New"},
        {"value": "investigating", "label": "Investigating"},
        {"value": "monitoring", "label": "Monitoring"},
        {"value": "resolved", "label": "Resolved"},
    ]
    issues = [
        {"issue_id": 1, "name": "M365 outage", "description": "Sign-in fails", "updated_at_iso": "2026-09-30T01:00:00+00:00",
         "assignments": [
             {"assignment_id": 11, "company_id": 5, "company_name": "Acme", "status": "investigating", "updated_at_iso": None},
             {"assignment_id": 12, "company_id": 6, "company_name": "Globex", "status": "resolved", "updated_at_iso": None},
         ]},
        {"issue_id": 2, "name": "Printer driver", "description": None, "updated_at_iso": None,
         "assignments": [
             {"assignment_id": 21, "company_id": 5, "company_name": "Acme", "status": "monitoring", "updated_at_iso": None},
         ]},
        {"issue_id": 3, "name": "Old VPN", "description": None, "updated_at_iso": None, "assignments": []},
    ]
    context = {
        "request": SimpleNamespace(url=SimpleNamespace(path="/admin/issues", query="")),
        "issues": issues,
        "issue_count": len(issues),
        "issue_status_options": statuses,
        "selected_status": None,
        "selected_company_id": None,
        "search_term": "",
        "company_options": [{"id": 5, "name": "Acme"}, {"id": 6, "name": "Globex"}],
        "editing_issue": None,
        "csrf_token": "t",
    }
    context.update(overrides)
    return context


def test_issue_tracker_renders_issue_list_stats_and_editor():
    html = _page_env().get_template("admin/issues.html").render(**_issue_tracker_context())

    # One row per issue (not per company), with its health and company statuses.
    assert html.count("data-iss-item") == 3
    assert "iss-item--active" in html and "iss-item--monitoring" in html and "iss-item--unlinked" in html
    assert 'action="/admin/issues/1/assignments/11/status"' in html
    assert 'action="/admin/issues/1/assignments/12/delete"' in html
    assert "stat-strip__stat--total" in html
    assert 'href="/admin/issues?issueId=2" data-iss-edit="2"' in html
    # Standard popup modal pattern.
    assert re.search(r'<div class="modal scf-modal" id="iss-modal" role="dialog" aria-modal="true" '
                     r'aria-labelledby="iss-modal-title" aria-hidden="true" hidden>', html)
    assert 'name="companyIds"' in html
    data = _json_block(html, "iss-editor-data")
    assert data["openIssueId"] is None
    assert data["issues"][0]["assignments"][1] == {"company_id": 6, "company_name": "Globex", "status": "resolved"}


def test_issue_tracker_empty_and_filtered_states():
    empty = _page_env().get_template("admin/issues.html").render(
        **_issue_tracker_context(issues=[], issue_count=0)
    )
    assert "No issues yet" in empty
    assert "+ Create your first issue" in empty

    filtered = _page_env().get_template("admin/issues.html").render(
        **_issue_tracker_context(issues=[], issue_count=0, selected_status="resolved")
    )
    assert "No issues match these filters" in filtered
    assert "Matching issues" in filtered


def test_issue_tracker_opens_editor_for_requested_issue():
    context = _issue_tracker_context()
    editing = dict(context["issues"][2], issue_id=9, name="Hidden by filters")
    html = _page_env().get_template("admin/issues.html").render(
        **dict(context, editing_issue=editing)
    )
    data = _json_block(html, "iss-editor-data")
    assert data["openIssueId"] == 9
    assert data["issues"][-1]["name"] == "Hidden by filters"
