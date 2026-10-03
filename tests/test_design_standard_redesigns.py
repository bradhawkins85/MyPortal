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


def test_out_of_office_hides_accounts_without_mailbox_by_default():
    mailboxes = [
        {"display_name": "Jane Doe", "user_principal_name": "jane@example.com"},
        {"display_name": "Shared Room", "user_principal_name": "room@example.com"},
    ]
    states = [
        {"mailbox": "jane@example.com", "success": True, "error": None, "no_mailbox": False,
         "setting": {"status": "disabled"}},
        {"mailbox": "room@example.com", "success": False, "setting": None, "no_mailbox": True,
         "error": "Microsoft Graph request failed (404): The mailbox is either inactive"},
    ]
    html = _page_env().get_template("m365/out_of_office.html").render(
        mailboxes=mailboxes, states=states, state_error=None, outcomes=[], submitted={},
        can_write=True, csrf_token="t",
    )

    assert 'data-oof-show-all' in html
    assert '1 without a mailbox hidden' in html
    assert '<dd data-oof-stat="total">1</dd>' in html
    room = html.split('data-mailbox="room@example.com"', 1)[1].split("</li>", 1)[0]
    assert "data-oof-no-mailbox hidden" in room
    assert "Couldn't read" not in html.split('data-mailbox="room@example.com"', 1)[1].split("data-oof-item", 1)[0]
    assert 'value="room@example.com" data-oof-select' not in html
    assert 'name="mailboxes" value="room@example.com"' not in html
    assert 'name="mailboxes" value="jane@example.com"' in html


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


def _message_template_page(page: str, **context) -> str:
    source = (TEMPLATES / page).read_text()
    imports = "".join(line + "\n" for line in source.splitlines() if line.startswith("{% from "))
    body = source[source.index("{% block content %}") + len("{% block content %}") :]
    body = body[: body.index("{% endblock %}\n\n{% block scripts %}")]
    return _env().from_string(imports + body).render(**context)


def _message_template_form_context(template: dict) -> dict:
    from app.features.message_templates import routes

    return {
        "template": template,
        "content_type_options": routes._content_type_options(),
        "default_content_type": template.get("content_type") or "text/plain",
        "common_variables": routes._variable_options(routes._COMMON_VARIABLES),
        "system_uses": routes._system_uses_payload(),
    }


def test_message_templates_list_renders_items_tokens_and_delete_modal():
    from app.features.message_templates import routes

    templates = [
        {"id": 1, "slug": "signup_verification", "name": "Verify email", "description": "Sent at sign-up",
         "content_type": "text/html", "updated_at_iso": "2026-09-30T10:00:00+00:00",
         "system_use": "Sign-up verification email"},
        {"id": 2, "slug": "ticket.resolved", "name": "Resolved", "description": None,
         "content_type": "text/plain", "updated_at_iso": None, "system_use": None},
    ]
    html = _message_template_page(
        "admin/message_templates.html",
        templates=templates,
        filters={"search": "", "content_type": ""},
        content_type_options=routes._content_type_options(),
        template_counts={"total": 2, "html": 1, "text": 1, "system": 1},
    )

    assert html.count("data-mt-item") == 2
    assert 'data-mt-copy="{{ template.ticket.resolved }}"' in html
    assert "Used for sign-up verification email" in html
    assert 'data-template-system="Sign-up verification email"' in html
    assert 'href="/admin/message-templates/2/edit"' in html
    assert "stat-strip" in html and "Used by MyPortal emails" in html
    assert '<div class="modal" id="mt-delete-modal" role="dialog" aria-modal="true"' in html
    assert "<dialog" not in html


def test_message_templates_list_empty_states():
    from app.features.message_templates import routes

    context = {
        "templates": [],
        "content_type_options": routes._content_type_options(),
        "template_counts": {"total": 0, "html": 0, "text": 0, "system": 0},
    }
    empty = _message_template_page(
        "admin/message_templates.html", filters={"search": "", "content_type": ""}, **context
    )
    filtered = _message_template_page(
        "admin/message_templates.html", filters={"search": "zzz", "content_type": ""}, **context
    )

    assert "+ Create your first template" in empty
    assert "No templates match these filters" in filtered
    assert 'data-mt-server-filtered="true"' in filtered


def test_message_template_editor_offers_variables_for_system_templates():
    html = _message_template_page(
        "admin/message_template_form.html",
        **_message_template_form_context(
            {"id": 4, "slug": "staff_invitation", "name": "Invite", "content_type": "text/html",
             "content": "<p>Hi {{ user.first_name }}</p>"}
        ),
    )

    assert 'data-mt-var="{{ invitation.link }}"' in html
    assert "MyPortal sends this as the <strong data-mt-system-label>staff invitation email</strong>" in html
    assert 'value="text/html" checked' in html
    assert "Save changes" in html and "data-mt-delete" in html
    assert 'id="mt-delete-modal"' in html
    data = _json_block(html, "mt-editor-data")
    assert "signup_verification" in data["systemUses"]


def test_message_template_editor_for_new_template():
    html = _message_template_page(
        "admin/message_template_form.html", **_message_template_form_context({})
    )

    assert "Create template" in html
    assert 'data-mt-var="{{ company.name }}"' in html
    assert 'data-mt-var="{{ invitation.link }}"' not in html
    assert 'value="text/plain" checked' in html
    assert "mt-delete-modal" not in html


def test_message_template_system_uses_match_the_slugs_myportal_sends():
    from app.features.message_templates.routes import SYSTEM_TEMPLATE_USES
    from app.services import subscription_renewals

    sources = Path("app/api/routes/auth.py").read_text() + Path("app/features/staff/handlers.py").read_text()
    assert '_render_email_template("signup_verification"' in sources
    assert '"staff_invitation", template_context' in sources
    renewal_slugs = {
        subscription_renewals._RENEWAL_TEMPLATE_SLUG,
        subscription_renewals._MONTHLY_RENEWAL_TEMPLATE_SLUG,
        subscription_renewals._THIRD_PARTY_ANNUAL_TEMPLATE_SLUG,
        subscription_renewals._THIRD_PARTY_MONTHLY_TEMPLATE_SLUG,
    }
    assert renewal_slugs | {"signup_verification", "staff_invitation"} == set(SYSTEM_TEMPLATE_USES)


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
    assert 'data-iss-delete hidden>Delete issue</button>' in html
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


def test_service_status_admin_renders_service_list_and_editor():
    from datetime import datetime

    definitions = [
        {"value": "operational", "label": "Operational", "description": "Working", "variant": "status--operational"},
        {"value": "outage", "label": "Major outage", "description": "Down", "variant": "status--outage"},
    ]
    service = {
        "id": 3, "name": "Email", "description": "Exchange Online", "status": "outage",
        "status_message": "Some users can't send", "display_order": 2, "is_active": False,
        "company_ids": [1, 2, 9], "tags": ["mail", "m365", "critical", "external", "smtp"], "updated_at": datetime(2026, 9, 1, 3, 4),
        "ai_lookup_enabled": True, "ai_lookup_url": "https://status.example.com", "ai_lookup_prompt": "",
        "ai_lookup_model_override": "", "ai_lookup_frequency_operational": 60,
        "ai_lookup_frequency_degraded": 15, "ai_lookup_frequency_partial_outage": 10,
        "ai_lookup_frequency_outage": 5, "ai_lookup_frequency_maintenance": 60,
        "ai_lookup_last_checked_at": None, "ai_lookup_last_status": None, "ai_lookup_last_message": None,
    }
    html = _page_env().get_template("admin/service_status.html").render(
        service_status_entries=[service],
        service_status_summary={"total": 1, "by_status": {"operational": 0, "outage": 1}},
        service_status_definitions=definitions,
        service_status_lookup={d["value"]: d for d in definitions},
        company_options=[{"id": 1, "name": "Acme"}, {"id": 2, "name": "Globex"}],
        service_status_company_lookup={1: "Acme", 2: "Globex"},
        service_status_public_urls={1: "/service-status/public/1/abc"},
        service_status_editing=service,
        service_status_default="operational",
        csrf_token="t",
    )

    # Edit links work without JavaScript and open the editor with it.
    assert 'href="/admin/service-status?serviceId=3" data-ssa-edit="3"' in html
    assert "Acme, Globex + 1 more" in html
    assert "Hidden from dashboards" in html
    assert "+ 1 tags" in html
    assert "AI checks every 5 min" in html
    assert '<time data-utc="2026-09-01T03:04:00Z">' in html
    assert 'id="service-modal"' in html and 'data-ssa-tab="visibility"' in html
    assert 'name="companyIds" value="2"' in html
    assert 'name="status" value="outage"' in html
    assert 'id="ssa-delete-form"' in html
    assert "service_status_admin.js" in html
    data = _json_block(html, "service-status-editor-data")
    assert data["services"][0]["tags"] == ["mail", "m365", "critical", "external", "smtp"]
    assert data["editing"]["id"] == 3
    assert data["frequencyDefaults"]["outage"] == 5
    assert "strftime" not in (TEMPLATES / "admin/service_status.html").read_text()


def test_service_status_admin_empty_state_offers_first_service():
    html = _page_env().get_template("admin/service_status.html").render(
        service_status_entries=[],
        service_status_summary={"total": 0, "by_status": {}},
        service_status_definitions=[],
        service_status_lookup={},
        company_options=[],
        service_status_company_lookup={},
        service_status_public_urls={},
        service_status_editing=None,
        service_status_default="operational",
        csrf_token="t",
    )

    assert "No services yet" in html
    assert "+ Add your first service" in html
    assert 'id="public-pages-modal"' not in html


def _render_roles(roles):
    from app.security.menu_permissions import catalogue_for_api

    env = _env()
    template = env.get_template("admin/roles.html")
    context = template.new_context({
        "roles": roles,
        "menu_permission_catalogue": catalogue_for_api(),
        "companies": [],
        "overview_company_id": None,
        "overview_role_id": None,
        "published_content_overview": [],
        "counter_strip": env.get_template("macros/counters.html").module.counter_strip,
    })
    return "".join(template.blocks["content"](context))


def test_roles_page_renders_catalogue_and_grouped_access_editor():
    html = _render_roles([
        {"id": 1, "name": "Staff", "description": "Everyday staff", "is_system": True,
         "member_count": 12, "permissions": {"menu.tickets": "read", "menu.dashboard": "read"}},
        {"id": 7, "name": "Helpdesk", "description": None, "is_system": False,
         "member_count": 0, "permissions": {"menu.admin.technician": "write", "menu.tickets": "write"}},
    ])

    assert 'data-role-edit="7"' in html and 'data-role-clone="1"' in html
    assert "12 members" in html and "Not assigned" in html
    assert "rol-chip--elevated" in html  # Technician is called out on the list
    assert 'class="modal scf-modal" id="role-modal" role="dialog"' in html
    # Every catalogue permission gets one radio group; tickets and technician keep their wording.
    ticket_row = html[html.index('data-role-perm="menu.tickets"'):]
    ticket_row = ticket_row[: ticket_row.index("</li>")]
    assert ">Own<" in ticket_row and ">All<" in ticket_row
    tech_row = html[html.index('data-role-perm="menu.admin.technician"'):]
    tech_row = tech_row[: tech_row.index("</li>")]
    assert ">No<" in tech_row and ">Yes<" in tech_row and 'value="read"' not in tech_row
    data = _json_block(html, "role-editor-data")
    assert data["roles"][1] == {
        "id": 7, "name": "Helpdesk", "description": "",
        "permissions": {"menu.admin.technician": "write", "menu.tickets": "write"},
        "isSystem": False, "members": 0,
    }
    assert {"key": "menu.tickets", "levels": ["none", "read", "write"]}.items() <= data["catalogue"][
        [item["key"] for item in data["catalogue"]].index("menu.tickets")
    ].items()


def test_roles_page_shows_group_overflow_chip_count():
    from app.security.menu_permissions import catalogue_for_api

    permissions = {}
    groups_seen = set()
    for permission in catalogue_for_api():
        group = permission["group"]
        if group in groups_seen:
            continue
        groups_seen.add(group)
        permissions[permission["key"]] = "read"
        if len(groups_seen) == 5:
            break

    html = _render_roles([
        {
            "id": 9,
            "name": "Overflow",
            "description": None,
            "is_system": False,
            "member_count": 1,
            "permissions": permissions,
        }
    ])

    assert "+1 more" in html


def test_roles_page_empty_state_offers_first_role():
    html = _render_roles([])

    assert "No roles yet" in html
    assert "+ Add your first role" in html


def _scheduled_tasks_context(**overrides):
    tasks = [
        {"id": 1, "name": "Acme — Sync to Xero", "command": "sync_to_xero", "command_label": "Sync to Xero",
         "command_group": "Billing and Xero", "company_id": 42, "company_name": "Acme",
         "company_edit_url": "/admin/companies/42/edit", "cron": "1 15 L * *", "description": None,
         "active": True, "exclude_from_calendar": False, "max_retries": 12, "retry_backoff_seconds": 300,
         "last_status": "failed", "last_error": "Xero token expired", "last_run_iso": "2026-09-01T15:01:00+00:00",
         "next_run_iso": "2026-09-30T15:01:00+00:00"},
        {"id": 2, "name": "All companies — Sync staff directory", "command": "sync_staff",
         "command_label": "Sync staff directory", "command_group": "Staff and assets", "company_id": None,
         "company_name": "All companies", "company_edit_url": None, "cron": "30 3 * * *",
         "description": "Nightly", "active": False, "exclude_from_calendar": True, "max_retries": 0,
         "retry_backoff_seconds": 60, "last_status": None, "last_error": None, "last_run_iso": None,
         "next_run_iso": None},
    ]
    context = {
        "tasks": tasks,
        "show_inactive": True,
        "upgrade_status": {"configured_mode": "graceful"},
        "command_options": [
            {"value": "sync_staff", "label": "Sync staff directory", "group": "Staff and assets"},
            {"value": "create_scheduled_ticket", "label": "Create scheduled ticket", "group": "Tickets"},
            {"value": "sync_to_xero", "label": "Sync to Xero", "group": "Billing and Xero"},
        ],
        "company_options": [{"value": "", "label": "All companies"}, {"value": "42", "label": "Acme"}],
        "bulk_company_options": [{"value": "42", "label": "Acme"}],
        "schedule_timezone": "Australia/Brisbane",
        "csrf_token": "t",
    }
    context.update(overrides)
    return context


def test_scheduled_tasks_render_grouped_list_with_editor_modals():
    html = _page_env().get_template("admin/scheduled_tasks.html").render(**_scheduled_tasks_context())

    # Tasks are grouped by what they do, with status and schedule readable at a glance.
    groups = re.findall(r'class="scf-group__title sch-group__title"[^>]*>\s*([^<]+?)\s*<', html)
    assert groups == ["Sync staff directory", "Sync to Xero"]
    assert 'data-sch-group' in html and 'data-sch-item' in html
    assert 'data-status="failed"' in html and 'data-status="paused"' in html
    assert "Xero token expired" in html
    assert 'data-sch-cron-summary="1 15 L * *"' in html
    assert 'data-utc="2026-09-01T15:01:00+00:00"' in html
    assert "Hidden from calendar" in html
    assert "Australia/Brisbane" in html
    # Header uses the shared actions macro with a single primary action.
    assert html.count("button--primary") == 1
    assert "data-task-create" in html and "data-bulk-task-create" in html
    # Every modal follows the div.modal standard (see docs/design_audit_scan.py).
    for modal_id in (
        "task-editor-modal", "bulk-task-create-modal", "scheduled-tasks-redistribute-modal",
        "task-logs-modal", "task-preview-modal",
    ):
        match = re.search(rf'<div class="modal[^"]*" id="{modal_id}"[^>]*>', html)
        assert match, modal_id
        for attribute in ('role="dialog"', 'aria-modal="true"', "aria-labelledby=", 'aria-hidden="true"', " hidden"):
            assert attribute in match.group(0), (modal_id, attribute)
    assert "<dialog" not in html
    # Editor keeps the JSON API field names; commands are grouped by category.
    assert 'id="task-command" name="command" required data-initial-focus' in html
    assert '<optgroup label="Billing and Xero">' in html
    assert 'name="cron"' in html and 'name="maxRetries"' in html and 'name="excludeFromCalendar"' in html
    assert 'id="task-json-payload"' in html and 'id="bulk-task-json-payload"' in html
    # Bulk forms still post the same fields.
    assert 'action="/admin/scheduled-tasks/bulk-create"' in html and 'name="companyIds" value="42"' in html
    assert 'data-scheduled-tasks-redistribute-hour' in html and 'data-scheduled-tasks-redistribute-offset' in html
    data = _json_block(html, "scheduled-tasks-data")
    assert data["timezone"] == "Australia/Brisbane"
    assert data["tasks"][0]["cron"] == "1 15 L * *"
    assert data["commandDefaults"]["generate_invoice"] == "1 0 L * *"
    assert "scheduled_tasks.js" in html and "automation.js" not in html
    # The stat strip filters the list: each tile is a toggle button keyed to a row attribute.
    assert 'data-sch-filter-strip' in html
    tiles = re.findall(r'data-sch-status="([a-z]+)" aria-pressed="false"', html)
    # Tiles only appear when they would match something (no task has succeeded here).
    assert tiles == ["failed", "never", "due", "paused", "hidden"]
    assert 'data-sch-status-clear aria-pressed="true"' in html
    assert 'data-last-status="failed"' in html and 'data-last-status="never"' in html
    # Task types can be shown or hidden from a checklist dropdown.
    assert 'data-sch-types' in html
    types = re.findall(r'<input type="checkbox" value="([^"]+)" data-sch-type checked', html)
    assert types == ["Sync staff directory", "Sync to Xero"]
    assert 'data-type="Sync to Xero"' in html


def test_scheduled_tasks_empty_state_offers_first_task():
    html = _page_env().get_template("admin/scheduled_tasks.html").render(
        **_scheduled_tasks_context(tasks=[], show_inactive=False)
    )
    assert "No active scheduled tasks" in html
    assert "+ Add your first task" in html
    # Paused tasks stay reachable from the toolbar switch.
    assert 'name="show_inactive" value="1" data-sch-show-inactive' in html


def _render_profile(**context) -> str:
    from types import SimpleNamespace

    from app import main as main_module

    values = {
        "request": SimpleNamespace(url=SimpleNamespace(path="/admin/profile", query=""), query_params={}),
        "app_name": "MyPortal", "csrf_token": "t", "is_super_admin": False,
        "has_authenticated_user": True, "active_membership": {}, "available_companies": [],
        "module_enabled": {}, "enabled_module_slugs": [],
        "current_user": {"id": 7, "email": "tech@example.com", "booking_link_url": "https://cal.example/me"},
        "profile_totp_devices": [{"id": 1, "name": "Phone"}],
        "profile_passkeys": [],
        "profile_m365_contacts": {"connected": True, "account_email": "tech@example.com"},
        "profile_show_technician_tools": True,
        "matrix_chat_enabled": True,
    }
    values.update(context)
    return main_module.templates.env.get_template("admin/profile.html").render(**values)


def test_profile_page_uses_tabs_stats_and_modals():
    html = _render_profile()

    # Page-level actions live in the header, and the page title isn't repeated in a card.
    assert 'data-profile-open="profile-password-modal"' in html
    assert "management__title" not in html
    # Security summary counts both second-factor methods.
    assert re.search(r'data-profile-stat="total"[^>]*>\s*<span[^>]*>Sign-in methods</span>\s*<span[^>]*>1</span>', html)
    for tab in ("security", "details", "integrations", "menu"):
        assert f'data-profile-tab="{tab}"' in html and f'data-profile-panel="{tab}"' in html
    # The tab bar is enhanced by JavaScript; without it every panel stays visible.
    assert re.search(r'data-profile-tabs\s+hidden', html)
    assert not re.search(r'data-profile-panel="\w+"[^>]*hidden', html)
    # Contact, booking and Matrix details save together from one form.
    contact = html[html.index('id="profile-contact-form"'):html.index("</form>", html.index('id="profile-contact-form"'))]
    for field in ('id="mobile-number"', 'id="booking-link-url"', 'id="matrix-user-id"'):
        assert field in contact
    assert contact.count('type="submit"') == 1
    # Existing account with an authenticator must confirm their password to add another.
    assert not re.search(r'data-totp-password-field\s+hidden', html)
    assert "data-profile-no-second-factor hidden" in html


def test_profile_modals_follow_the_gold_standard():
    html = _render_profile()
    modal_ids = re.findall(r'<div class="modal scf-modal" id="([\w-]+)" role="dialog" aria-modal="true" aria-labelledby="[\w-]+"[^>]* hidden>', html)
    assert modal_ids == [
        "profile-password-modal", "profile-totp-modal", "profile-passkey-modal",
        "profile-rename-modal", "profile-confirm-modal", "profile-anonymise-modal",
    ]
    assert "<dialog" not in html
    for hook in (
        'id="password-form"', "data-password-rules", 'name="confirm_password"',
        "data-totp-qr", "data-totp-manual-toggle", 'id="totp-verify-form"', 'id="totp-code"',
        'id="passkey-add-form"', 'id="passkey-rename-form"', 'id="profile-confirm-form"',
        "auth_ui.js", "passkey_utils.js", "profile.js",
        'id="profile-anonymise-form"', 'name="confirm_email"', 'name="acknowledge"',
    ):
        assert hook in html, hook


def test_profile_standard_user_sees_security_and_menu_only():
    html = _render_profile(profile_show_technician_tools=False, profile_totp_devices=[])
    assert 'data-profile-tab="details"' not in html
    assert 'data-profile-tab="integrations"' not in html
    assert "rich_text_editor.js" not in html
    assert re.search(r'data-totp-password-field\s+hidden', html)
    assert "data-profile-no-second-factor hidden" not in html


def _render_webhooks(**context) -> str:
    stub_base = (
        "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}"
        "{% block scripts %}{% endblock %}"
    )
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [jinja2.DictLoader({"base.html": stub_base}), jinja2.FileSystemLoader(str(TEMPLATES))]
        ),
        autoescape=True,
    )
    env.globals["static_url"] = lambda path: path
    defaults = {
        "events": [],
        "webhook_search": "",
        "webhook_status": "",
        "webhook_event_limit": 1000,
        "webhook_event_limit_options": (200, 500, 1000),
        "webhook_status_counts": {"failed": 2, "pending": 1, "in_progress": 0, "succeeded": 7},
        "deletion_rules": [],
        "retention": {"enabled": True, "retention_value": 30, "retention_unit": "days"},
    }
    defaults.update(context)
    return env.get_template("admin/webhooks.html").render(**defaults)


def test_webhook_monitor_renders_stats_log_and_rule_summaries():
    events = [
        {"id": 5, "name": "ticket.created", "target_url": "https://hooks.example/a", "status": "failed",
         "direction": "outgoing", "attempt_count": 3, "max_attempts": 3, "last_error": "HTTP 500",
         "next_attempt_iso": None, "updated_iso": "2026-09-30T10:00:00+00:00"},
        {"id": 6, "name": "sync", "target_url": "", "status": "in_progress", "direction": "incoming",
         "attempt_count": 1, "max_attempts": 3, "last_error": None, "next_attempt_iso": None,
         "updated_iso": None},
    ]
    rules = [{"id": 9, "name": "Drop pings", "execution_type": "scheduled", "cron_expression": "0 2 * * *",
              "enabled": True, "next_run_at": "2026-10-01T02:00:00+00:00", "last_run_at": None,
              "conditions": [{"field": "status", "operator": "equals", "value": "succeeded"},
                             {"field": "payload.ping", "operator": "is_not_empty", "value": None}]}]
    html = _render_webhooks(events=events, deletion_rules=rules)

    # Stat strip totals the whole queue and links each state to its filter.
    assert 'stat-strip__stat--total' in html and ">10<" in html
    assert 'href="/admin/webhooks?status=failed&amp;event_limit=1000"' in html
    # Status pills and exhausted attempts.
    assert 'status status--error">Failed' in html
    assert "whm-attempts--exhausted" in html
    # In-progress rows cannot be deleted mid-delivery.
    row = html[html.index('data-event-id="6"'):]
    assert row.index("data-webhook-delete disabled") < row.index("</tr>")
    # Rules render as a list with a plain-English summary.
    assert 'data-deletion-rule data-rule=' in html
    assert "Deletes entries where <code>status</code> is “succeeded” and <code>payload.ping</code> is not empty." in html
    assert "Entries created more than 30 days ago" in html
    # Modals follow the div.modal standard.
    for modal_id in ("webhook-deletion-rule-modal", "webhook-attempts-modal"):
        assert f'<div class="modal scf-modal" id="{modal_id}" role="dialog" aria-modal="true"' in html
    assert "<dialog" not in html


def test_webhook_monitor_empty_states():
    html = _render_webhooks()
    assert 'id="webhooks-table"' not in html
    assert "No webhook activity recorded" in html
    assert "No deletion rules yet" in html

    filtered = _render_webhooks(webhook_search="abc", webhook_status="failed")
    assert "No webhook events match" in filtered
    assert "Dead-letter queue" in filtered


def _render_campaign_form(**context) -> str:
    stub_base = (
        "{% block header_title %}{% endblock %}{% block header_actions %}{% endblock %}"
        "{% block content %}{% endblock %}"
    )
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [jinja2.DictLoader({"base.html": stub_base}), jinja2.FileSystemLoader(str(TEMPLATES))]
        ),
        autoescape=True,
    )
    defaults = {
        "campaign": None,
        "campaign_values": {},
        "audience": {"company_mode": "selected", "company_ids": [], "exclude_company_ids": [],
                     "asset_field_ids": [], "product_ids": [], "contact_scope": "billing",
                     "job_titles": [], "departments": [], "include_emails": [], "exclude_emails": []},
        "campaign_variables": [],
        "csrf_token": "t",
    }
    defaults.update(context)
    return env.get_template("admin/marketing_campaign_form.html").render(**defaults)


def test_campaign_form_puts_title_status_and_save_in_header_bar():
    html = _render_campaign_form()
    header = html[: html.index("<form")]
    assert 'page-header-bar page-header-bar--record' in header
    assert ">New campaign</span>" in header
    assert 'status status--neutral">Draft' in header
    # Back link sits left of the primary Save, which submits the page form.
    assert header.index("Back to campaigns") < header.index("Save and preview recipients")
    assert 'type="submit" class="button button--primary" form="campaign-form"' in header
    assert 'id="campaign-form"' in html
    # The drafts note and the Save button are not repeated in the content.
    body = html[html.index("<form"):]
    assert "Save and preview recipients" not in body
    assert "stay as drafts" not in body

    edit = _render_campaign_form(campaign={"id": 7, "name": "Spring update", "status": "draft"})
    edit_header = edit[: edit.index("<form")]
    assert ">Spring update</span>" in edit_header
    assert 'href="/admin/marketing/campaigns/7">Back to campaign' in edit_header
    assert 'action="/admin/marketing/campaigns/7"' in edit
