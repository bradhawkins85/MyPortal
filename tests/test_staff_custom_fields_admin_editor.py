"""Tests for the staff custom field admin editor (company edit page)."""

import json
from pathlib import Path

from app.features.companies.handlers import (
    _custom_field_options_from_form,
    _parse_staff_custom_field_condition,
)


TEMPLATE = Path("app/templates/admin/company_edit.html")


def _staff_custom_fields_section(template: str) -> str:
    start = template.index('<h2 class="card__title">Staff custom fields</h2>')
    end = template.index("data-scf-delete-form", start)
    return template[start:end]


def test_options_json_preserves_delimiter_characters():
    form = {
        "options_json": json.dumps(
            [
                {"value": "dell", "label": "Dell XPS, 15\"", "m365_upn": ""},
                {"value": "", "label": "Label: with colon", "m365_upn": "A@Example.com|Group Name"},
                {"value": "DELL", "label": "Duplicate value", "m365_upn": ""},
                {"value": "", "label": "", "m365_upn": ""},
                "not-an-object",
            ]
        ),
        "options": "ignored:Ignored",
    }

    assert _custom_field_options_from_form(form) == [
        {"value": "dell", "label": "Dell XPS, 15\"", "m365_upn": ""},
        {
            "value": "Label: with colon",
            "label": "Label: with colon",
            "m365_upn": "a@example.com|group name",
        },
    ]


def test_empty_options_json_list_clears_options():
    assert _custom_field_options_from_form({"options_json": "[]", "options": "a, b"}) == []


def test_legacy_options_string_still_supported():
    form = {"options": "small:Small|mailbox@example.com, medium"}

    assert _custom_field_options_from_form(form) == [
        {"value": "small", "label": "Small", "m365_upn": "mailbox@example.com"},
        {"value": "medium", "label": "medium", "m365_upn": ""},
    ]


def test_invalid_options_json_falls_back_to_legacy_string():
    form = {"options_json": "{not json", "options": "a:A"}

    assert _custom_field_options_from_form(form) == [
        {"value": "a", "label": "A", "m365_upn": ""}
    ]


def test_select_map_with_array_values_is_normalized():
    parent, operator, value = _parse_staff_custom_field_condition(
        parent_name_value="department",
        operator_value="select_map",
        condition_value='{"sales": ["Dell XPS, 15\\""], "fallback": ["other"]}',
    )

    assert parent == "department"
    assert operator == "select_map"
    assert json.loads(value) == {"sales": ['Dell XPS, 15"'], "fallback": ["other"]}


def test_staff_custom_fields_use_list_and_modal_editor():
    template = TEMPLATE.read_text()
    section = _staff_custom_fields_section(template)

    assert "staff-custom-fields-table" not in template
    assert "data-scf-create" in section
    assert "data-scf-edit=" in section
    assert 'id="staff-custom-field-modal"' in section
    assert 'id="staff-custom-field-editor-data"' in section
    for field_name in (
        "options_json",
        "condition_parent_name",
        "condition_operator",
        "condition_value",
        "visible_to_job_titles",
        "visible_to_requester_emails",
        "m365_upn",
        "display_name",
        "help_text",
        "field_group",
        "display_order",
        "is_active",
        "field_type",
    ):
        assert f'name="{field_name}"' in section
    assert "static/js/staff_custom_fields_admin.js" in template


def test_staff_custom_field_modal_is_outside_collapsible_panel():
    template = TEMPLATE.read_text()
    panel_start = template.index("data-staff-custom-fields-panel")
    panel_end = template.index("</details>", panel_start)
    modal = template.index('id="staff-custom-field-modal"')

    assert modal > panel_end
