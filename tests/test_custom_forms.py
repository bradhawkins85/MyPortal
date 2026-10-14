"""Tests for the Custom Forms feature.

Follows the :mod:`tests.test_client_onboarding` pattern: pure-function tests,
service-layer tests with monkeypatched repositories, direct route-handler
invocations (the handlers are plain async functions taking a ``user`` dict),
and an in-memory SQLite check that the migration applies cleanly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import forms as forms_api
from app.core.database import db
from app.repositories import forms as forms_repo
from app.schemas.forms import (
    FieldReorder,
    FormAccessUpdate,
    FormCreate,
    FormFieldCreate,
    FormUpdate,
)
from app.services import custom_forms
from app.services.custom_forms import FormValidationError

_ADMIN = {"id": 9, "is_super_admin": True}
_NON_ADMIN = {"id": 1, "is_super_admin": False}
_SUPER_ADMIN = {"id": 9, "is_super_admin": True, "company_id": 7}

_FORM = {"id": 1, "slug": "support", "name": "Support", "description": None, "active": True}


def _field(field_id, field_type="text", *, label="Issue", required=False, options=None, config=None):
    return {
        "id": field_id,
        "form_id": 1,
        "field_type": field_type,
        "label": label,
        "placeholder": None,
        "help_text": None,
        "required": required,
        "options": options or [],
        "default_value": None,
        "config": config or {},
        "sort_order": 0,
    }


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_forms_router_registers_admin_and_public_routes():
    routes = {
        (method, route.path)
        for route in forms_api.router.routes
        for method in (route.methods or set())
    }
    assert ("GET", "/api/forms") in routes
    assert ("POST", "/api/forms") in routes
    assert ("PATCH", "/api/forms/{form_id}") in routes
    assert ("DELETE", "/api/forms/{form_id}") in routes
    assert ("POST", "/api/forms/{form_id}/fields") in routes
    assert ("PUT", "/api/forms/{form_id}/fields/reorder") in routes
    assert ("GET", "/api/forms/{form_id}/access") in routes
    assert ("PUT", "/api/forms/{form_id}/access") in routes
    assert ("POST", "/forms/{slug}/submit") in routes


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------


def test_slugify_and_unique_slug():
    assert custom_forms.slugify("Hello World") == "hello-world"
    assert custom_forms.slugify("  Support Ticket  ") == "support-ticket"
    assert custom_forms.slugify("   ") == "form"

    assert custom_forms.unique_slug("Hello World", []) == "hello-world"
    assert custom_forms.unique_slug("Hello World", ["hello-world"]) == "hello-world-2"
    assert custom_forms.unique_slug(
        "Hello World", ["hello-world", "hello-world-2"]
    ) == "hello-world-3"


# ---------------------------------------------------------------------------
# Field definition validation
# ---------------------------------------------------------------------------


def test_validate_field_definition_accepts_valid_types():
    assert custom_forms.validate_field_definition("text", "Name", None) == []
    assert custom_forms.validate_field_definition("select", "Pick", ["a", "b"]) == []
    assert custom_forms.validate_field_definition("multiselect", "Tags", ["a", "b"]) == []


def test_validate_field_definition_reports_errors():
    assert custom_forms.validate_field_definition("bogus", "X", None) == [
        "Unknown field type: bogus"
    ]
    assert custom_forms.validate_field_definition("text", "   ", None) == [
        "Field label is required."
    ]
    assert custom_forms.validate_field_definition("select", "Pick", []) == [
        "This field type requires at least one option."
    ]
    assert custom_forms.validate_field_definition("text", "Name", None) == []


def test_validate_field_definition_new_types():
    # time / url / password / signature / hidden need no config.
    for ft in ("time", "url", "password", "signature", "hidden"):
        assert custom_forms.validate_field_definition(ft, "Label", []) == []
    # rating and audio fall back to sensible defaults when config is absent.
    assert custom_forms.validate_field_definition("rating", "Rate", None) == []
    assert custom_forms.validate_field_definition("audio", "Clips", None) == []
    # explicit config.
    assert custom_forms.validate_field_definition("rating", "Rate", None, {"max": 5}) == []
    assert custom_forms.validate_field_definition("audio", "Clips", None, {"max_clips": 3}) == []
    assert (
        custom_forms.validate_field_definition(
            "matrix", "Grid", None, {"rows": ["a"], "columns": ["x"]}
        )
        == []
    )


def test_validate_field_definition_new_type_errors():
    # matrix requires both rows and columns.
    assert custom_forms.validate_field_definition("matrix", "Grid", None, {}) == [
        "A matrix field requires at least one row.",
        "A matrix field requires at least one column.",
    ]
    errors = custom_forms.validate_field_definition("matrix", "Grid", None, {"rows": ["a"]})
    assert "A matrix field requires at least one column." in errors
    # too many rows.
    errors = custom_forms.validate_field_definition(
        "matrix", "Grid", None, {"rows": ["r"] * 51, "columns": ["c"]}
    )
    assert any("50 rows" in e for e in errors)
    # rating max out of range.
    assert any(
        "between 1 and 10" in e
        for e in custom_forms.validate_field_definition("rating", "Rate", None, {"max": 0})
    )
    assert any(
        "between 1 and 10" in e
        for e in custom_forms.validate_field_definition("rating", "Rate", None, {"max": 11})
    )
    # audio max_clips out of range.
    assert any(
        "clips" in e
        for e in custom_forms.validate_field_definition("audio", "Clips", None, {"max_clips": 0})
    )


# ---------------------------------------------------------------------------
# Submission validation (including multiselect handling)
# ---------------------------------------------------------------------------


def test_validate_submission_valid_values():
    fields = [
        _field(1, "email", label="Email", required=True),
        _field(2, "number", label="Qty"),
        _field(3, "date", label="When"),
    ]
    clean, errors = custom_forms.validate_submission(
        fields, {1: "a@b.com", 2: "5", 3: "2026-01-02"}
    )
    assert errors == []
    assert clean == {1: "a@b.com", 2: "5", 3: "2026-01-02"}


def test_validate_submission_type_errors():
    fields = [
        _field(1, "email", label="Email", required=True),
        _field(2, "number", label="Qty"),
        _field(3, "date", label="When"),
    ]
    _, errors = custom_forms.validate_submission(
        fields, {1: "not-an-email", 2: "abc", 3: "02/03/2026"}
    )
    assert any("valid email" in e for e in errors)
    assert any("number" in e for e in errors)
    assert any("valid date" in e for e in errors)


def test_validate_submission_required_missing():
    fields = [
        _field(1, "text", label="Name", required=True),
        _field(4, "checkbox", label="Consent", required=True),
    ]
    _, errors = custom_forms.validate_submission(fields, {1: "", 4: ""})
    assert "'Name' is required." in errors
    assert "'Consent' is required." in errors


def test_validate_submission_checkbox_truthiness():
    fields = [_field(4, "checkbox", label="Consent")]
    clean, errors = custom_forms.validate_submission(fields, {4: "on"})
    assert errors == []
    assert clean[4] is True
    clean, _ = custom_forms.validate_submission(fields, {4: ""})
    assert clean[4] is False


def test_validate_submission_multiselect():
    fields = [_field(7, "multiselect", label="Tags", required=True, options=["a", "b", "c"])]

    # A list of valid options passes.
    clean, errors = custom_forms.validate_submission(fields, {7: ["a", "c"]})
    assert errors == []
    assert clean[7] == ["a", "c"]

    # A single scalar is coerced to a one-item list.
    clean, errors = custom_forms.validate_submission(fields, {7: "b"})
    assert errors == []
    assert clean[7] == ["b"]

    # Blank collapses to an empty list and fails the required check.
    clean, errors = custom_forms.validate_submission(fields, {7: ""})
    assert clean[7] == []
    assert "'Tags' is required." in errors

    # An option that is not in the field's option set is rejected.
    _, errors = custom_forms.validate_submission(fields, {7: ["a", "zzz"]})
    assert any("Invalid option" in e for e in errors)


def test_validate_submission_hidden_field_uses_default():
    hidden = _field(9, "hidden", label="Token")
    hidden["default_value"] = "abc"
    clean, errors = custom_forms.validate_submission(fields=[hidden], values={})
    assert errors == []
    assert clean[9] == "abc"


def test_validate_submission_new_scalar_types():
    fields = [
        _field(1, "time", label="When", required=True),
        _field(2, "url", label="Site", required=True),
        _field(3, "password", label="Pass", required=True),
    ]
    clean, errors = custom_forms.validate_submission(
        fields, {1: "09:30", 2: "https://example.com", 3: "hunter2"}
    )
    assert errors == []
    assert clean == {1: "09:30", 2: "https://example.com", 3: "hunter2"}


def test_validate_submission_new_scalar_type_errors():
    fields = [
        _field(1, "time", label="When"),
        _field(2, "url", label="Site"),
        _field(3, "password", label="Pass", required=True),
    ]
    _, errors = custom_forms.validate_submission(
        fields, {1: "9:3", 2: "example.com", 3: ""}
    )
    assert any("time" in e for e in errors)
    assert any("URL" in e for e in errors)
    assert "'Pass' is required." in errors


def test_validate_submission_rating():
    fields = [_field(1, "rating", label="Rate", required=True, config={"max": 5})]
    clean, errors = custom_forms.validate_submission(fields, {1: "4"})
    assert errors == []
    assert clean[1] == 4
    # out-of-range value is clamped to the max and reported.
    clean, errors = custom_forms.validate_submission(fields, {1: "9"})
    assert clean[1] == 5
    assert any("between 0 and 5" in e for e in errors)
    # required and blank.
    _, errors = custom_forms.validate_submission(fields, {1: ""})
    assert "'Rate' is required." in errors


def test_validate_submission_signature():
    fields = [_field(1, "signature", label="Sign", required=True)]
    clean, errors = custom_forms.validate_submission(
        fields, {1: "data:image/png;base64,AAAA"}
    )
    assert errors == []
    assert clean[1].startswith("data:")
    # non-image rejected.
    _, errors = custom_forms.validate_submission(fields, {1: "not-an-image"})
    assert any("must be an image" in e for e in errors)
    # required and blank.
    _, errors = custom_forms.validate_submission(fields, {1: ""})
    assert "'Sign' is required." in errors


def test_validate_submission_matrix():
    fields = [
        _field(
            1,
            "matrix",
            label="Grid",
            config={"rows": ["a", "b"], "columns": ["yes", "no"]},
        )
    ]
    clean, errors = custom_forms.validate_submission(fields, {1: {"a": "yes", "b": "no"}})
    assert errors == []
    assert clean[1] == {"a": "yes", "b": "no"}
    # invalid row.
    _, errors = custom_forms.validate_submission(fields, {1: {"z": "yes"}})
    assert any("Invalid row" in e for e in errors)
    # invalid column.
    _, errors = custom_forms.validate_submission(fields, {1: {"a": "maybe"}})
    assert any("Invalid column" in e for e in errors)
    # required and empty.
    req = [
        _field(
            1,
            "matrix",
            label="Grid",
            required=True,
            config={"rows": ["a"], "columns": ["x"]},
        )
    ]
    _, errors = custom_forms.validate_submission(req, {1: {}})
    assert "'Grid' is required." in errors


def test_validate_submission_audio_multi_clip():
    fields = [_field(1, "audio", label="Clips", config={"max_clips": 3})]
    # up to max_clips passes.
    clean, errors = custom_forms.validate_submission(
        fields, {}, {1: [SimpleNamespace(filename=f"c{i}.mp3") for i in range(3)]}
    )
    assert errors == []
    assert len(clean[1]) == 3
    # exceeding max_clips is reported.
    _, errors = custom_forms.validate_submission(
        fields, {}, {1: [SimpleNamespace(filename=f"c{i}.mp3") for i in range(4)]}
    )
    assert any("at most 3 clips" in e for e in errors)
    # required and none.
    req = [_field(1, "audio", label="Clips", required=True)]
    _, errors = custom_forms.validate_submission(req, {}, {})
    assert "'Clips' is required." in errors


# ---------------------------------------------------------------------------
# Ticket description building
# ---------------------------------------------------------------------------


def test_build_description_includes_values_and_skips_hidden():
    fields = [
        _field(1, "text", label="Name"),
        _field(2, "multiselect", label="Tags"),
        _field(3, "hidden", label="Secret"),
    ]
    clean = {1: "Bob", 2: ["x", "y"], 3: "hidden-value"}
    description = custom_forms.build_description("My Form", fields, clean)
    assert "My Form" in description
    assert "Bob" in description
    assert "x, y" in description
    assert "hidden-value" not in description


def test_build_description_escapes_html():
    fields = [_field(1, "text", label="Name")]
    description = custom_forms.build_description("Form", fields, {1: "<script>"})
    assert "<script>" not in description
    assert "&lt;script&gt;" in description


def test_display_value_new_types():
    assert custom_forms._display_value(_field(1, "rating", config={"max": 5}), 4) == "4 / 5"
    assert (
        custom_forms._display_value(_field(1, "signature"), "data:image/png;base64,x")
        == "(signature image)"
    )
    assert custom_forms._display_value(_field(1, "matrix"), {"a": "yes"}) == "a: yes"
    assert (
        custom_forms._display_value(_field(1, "audio"), ["a.mp3", "b.mp3"])
        == "a.mp3, b.mp3"
    )


# ---------------------------------------------------------------------------
# submit_form → ticket + private attachment + submission record
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_submit_form_creates_ticket_and_private_attachment(monkeypatch):
    form = dict(_FORM)
    fields = [
        _field(1, "text", label="Issue", required=True),
        _field(2, "file", label="Attach"),
    ]
    create_ticket = AsyncMock(return_value={"id": 555})
    monkeypatch.setattr("app.services.tickets.create_ticket", create_ticket)
    save_file = AsyncMock(
        return_value={"id": 900, "filename": "log.txt", "size": 5, "content_type": "text/plain"}
    )
    monkeypatch.setattr("app.services.ticket_attachments.save_uploaded_file", save_file)
    create_submission = AsyncMock(return_value={"id": 77})
    monkeypatch.setattr(forms_repo, "create_submission", create_submission)
    monkeypatch.setattr(custom_forms, "log_info", lambda *a, **k: None)
    monkeypatch.setattr(custom_forms, "log_error", lambda *a, **k: None)

    uploaded = SimpleNamespace(filename="log.txt")
    result = await custom_forms.submit_form(
        form=form,
        fields=fields,
        values={1: "Something broke"},
        files={2: [uploaded]},
        requester=_SUPER_ADMIN,
        ip_address="1.2.3.4",
        user_agent="pytest",
    )

    assert result["ticket_id"] == 555
    assert result["submission_id"] == 77
    assert result["attachments"] == [
        {"id": 900, "filename": "log.txt", "size": 5, "content_type": "text/plain"}
    ]

    # A fresh, normal-priority ticket is created for the requester's company.
    ticket_kwargs = create_ticket.await_args.kwargs
    assert ticket_kwargs["status"] == "new"
    assert ticket_kwargs["priority"] == "normal"
    assert ticket_kwargs["requester_id"] == 9
    assert ticket_kwargs["company_id"] == 7
    assert ticket_kwargs["subject"] == "Form submission: Support"

    # The file is stored as a private (closed) attachment.
    file_kwargs = save_file.await_args.kwargs
    assert file_kwargs["ticket_id"] == 555
    assert file_kwargs["access_level"] == "closed"
    assert file_kwargs["uploaded_by_user_id"] == 9
    assert file_kwargs["file"] is uploaded

    # The submission is recorded and linked to the ticket.
    sub_kwargs = create_submission.await_args.kwargs
    assert sub_kwargs["form_id"] == 1
    assert sub_kwargs["ticket_id"] == 555
    assert sub_kwargs["submitted_by"] == 9
    assert sub_kwargs["ip_address"] == "1.2.3.4"


@pytest.mark.anyio
async def test_submit_form_validation_failure_raises_before_any_write(monkeypatch):
    create_ticket = AsyncMock()
    monkeypatch.setattr("app.services.tickets.create_ticket", create_ticket)
    fields = [_field(1, "email", label="Email", required=True)]
    with pytest.raises(FormValidationError):
        await custom_forms.submit_form(
            form=dict(_FORM),
            fields=fields,
            values={1: ""},
            files={},
            requester=_SUPER_ADMIN,
        )
    create_ticket.assert_not_awaited()


@pytest.mark.anyio
async def test_submit_form_requires_a_company():
    fields = [_field(1, "text", label="Issue")]
    with pytest.raises(FormValidationError) as exc:
        await custom_forms.submit_form(
            form=dict(_FORM),
            fields=fields,
            values={1: "x"},
            files={},
            requester={"id": 1},  # no company_id
        )
    assert "company" in str(exc.value).lower()


@pytest.mark.anyio
async def test_submit_form_ignores_attachment_errors(monkeypatch):
    form = dict(_FORM)
    fields = [_field(1, "text", label="Issue"), _field(2, "file", label="Attach")]
    create_ticket = AsyncMock(return_value={"id": 555})
    monkeypatch.setattr("app.services.tickets.create_ticket", create_ticket)
    save_file = AsyncMock(side_effect=RuntimeError("disk full"))
    monkeypatch.setattr("app.services.ticket_attachments.save_uploaded_file", save_file)
    create_submission = AsyncMock(return_value={"id": 77})
    monkeypatch.setattr(forms_repo, "create_submission", create_submission)
    monkeypatch.setattr(custom_forms, "log_info", lambda *a, **k: None)
    monkeypatch.setattr(custom_forms, "log_error", lambda *a, **k: None)

    result = await custom_forms.submit_form(
        form=form,
        fields=fields,
        values={1: "Issue"},
        files={2: [SimpleNamespace(filename="log.txt")]},
        requester=_SUPER_ADMIN,
    )
    assert result["ticket_id"] == 555
    assert result["attachments"] == []


# ---------------------------------------------------------------------------
# Access control (repo)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_can_access_form_open_when_no_access_rows(monkeypatch):
    monkeypatch.setattr(forms_repo, "has_access_rows", AsyncMock(return_value=False))
    assert await forms_repo.can_access_form(1, 999) is True


@pytest.mark.anyio
async def test_can_access_form_with_rows_grants_only_listed_users(monkeypatch):
    monkeypatch.setattr(forms_repo, "has_access_rows", AsyncMock(return_value=True))
    fetch_one = AsyncMock(return_value={"present": 1})
    monkeypatch.setattr(db, "fetch_one", fetch_one)
    assert await forms_repo.can_access_form(1, 5) is True
    fetch_one.return_value = None
    assert await forms_repo.can_access_form(1, 6) is False


@pytest.mark.anyio
async def test_can_access_form_super_admin_always_allowed():
    # Super admins bypass the access-row check entirely (no DB access needed).
    assert await forms_repo.can_access_form(1, 999, is_super_admin=True) is True


# ---------------------------------------------------------------------------
# Admin route handlers (direct invocation)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_list_forms_requires_super_admin():
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_list_forms(user=_NON_ADMIN)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_api_create_form_generates_unique_slug(monkeypatch):
    monkeypatch.setattr(
        forms_repo, "list_forms", AsyncMock(return_value=[{"slug": "support"}])
    )
    created = AsyncMock(
        return_value={"id": 1, "slug": "support-2", "name": "Support", "description": None, "active": True}
    )
    monkeypatch.setattr(forms_repo, "create_form", created)
    result = await forms_api.api_create_form(payload=FormCreate(name="Support"), user=_ADMIN)
    assert result["slug"] == "support-2"
    assert created.await_args.kwargs["slug"] == "support-2"
    assert created.await_args.kwargs["created_by"] == 9


@pytest.mark.anyio
async def test_api_get_form_returns_serialised(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(
        forms_repo, "list_fields", AsyncMock(return_value=[_field(10, "text", label="L")])
    )
    result = await forms_api.api_get_form(form_id=1, user=_ADMIN)
    assert result["id"] == 1
    assert result["slug"] == "support"
    assert result["fields"][0]["label"] == "L"


@pytest.mark.anyio
async def test_api_get_form_missing_is_404(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_get_form(form_id=1, user=_ADMIN)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_api_update_form(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    updated = AsyncMock(return_value=dict(_FORM, name="Renamed"))
    monkeypatch.setattr(forms_repo, "update_form", updated)
    result = await forms_api.api_update_form(
        form_id=1, payload=FormUpdate(name="Renamed"), user=_ADMIN
    )
    assert result["name"] == "Renamed"
    assert updated.await_args.args[0] == 1
    assert updated.await_args.kwargs["name"] == "Renamed"


@pytest.mark.anyio
async def test_api_delete_form(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    deleted = AsyncMock()
    monkeypatch.setattr(forms_repo, "delete_form", deleted)
    result = await forms_api.api_delete_form(form_id=1, user=_ADMIN)
    assert result == {"ok": True}
    deleted.assert_awaited_once_with(1)


@pytest.mark.anyio
async def test_api_create_field(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(forms_repo, "list_fields", AsyncMock(return_value=[_field(1)]))
    created = AsyncMock(return_value=_field(10, "select", label="Pick", options=["a", "b"]))
    monkeypatch.setattr(forms_repo, "create_field", created)
    result = await forms_api.api_create_field(
        form_id=1,
        payload=FormFieldCreate(field_type="select", label="Pick", options=["a", "b"]),
        user=_ADMIN,
    )
    assert created.await_args.kwargs["options"] == ["a", "b"]
    assert created.await_args.kwargs["sort_order"] == 1
    assert result["field_type"] == "select"


@pytest.mark.anyio
async def test_api_create_field_rejects_invalid_definition(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    # A multiselect without options is an invalid definition.
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_create_field(
            form_id=1,
            payload=FormFieldCreate(field_type="multiselect", label="Tags", options=[]),
            user=_ADMIN,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_api_delete_field(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(forms_repo, "get_field", AsyncMock(return_value=_field(10)))
    deleted = AsyncMock()
    monkeypatch.setattr(forms_repo, "delete_field", deleted)
    result = await forms_api.api_delete_field(form_id=1, field_id=10, user=_ADMIN)
    assert result == {"ok": True}
    deleted.assert_awaited_once_with(10)


@pytest.mark.anyio
async def test_api_reorder_fields_requires_every_field(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(
        forms_repo, "list_fields", AsyncMock(return_value=[_field(1), _field(2)])
    )
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_reorder_fields(
            form_id=1, payload=FieldReorder(field_ids=[1]), user=_ADMIN
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_api_reorder_fields(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(
        forms_repo, "list_fields", AsyncMock(return_value=[_field(1), _field(2)])
    )
    reorder = AsyncMock()
    monkeypatch.setattr(forms_repo, "reorder_fields", reorder)
    await forms_api.api_reorder_fields(
        form_id=1, payload=FieldReorder(field_ids=[2, 1]), user=_ADMIN
    )
    reorder.assert_awaited_once_with(1, [2, 1])


@pytest.mark.anyio
async def test_api_set_access_grants_and_revokes(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=dict(_FORM)))
    monkeypatch.setattr(
        forms_repo, "list_access_user_ids", AsyncMock(return_value=[1, 3])
    )
    grant = AsyncMock()
    revoke = AsyncMock()
    monkeypatch.setattr(forms_repo, "grant_access", grant)
    monkeypatch.setattr(forms_repo, "revoke_access", revoke)
    result = await forms_api.api_set_access(
        form_id=1, payload=FormAccessUpdate(user_ids=[3, 5]), user=_ADMIN
    )
    # current {1, 3}, desired {3, 5} → revoke 1, grant 5.
    revoke.assert_awaited_once_with(1, 1)
    grant.assert_awaited_once_with(1, 5)
    assert result == {"user_ids": [3, 5]}


# ---------------------------------------------------------------------------
# Public submit route guards
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_submit_form_unknown_or_inactive_is_404(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form_by_slug", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_submit_form(slug="nope", request=_form_request(), user=_NON_ADMIN)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_api_submit_form_denies_without_access(monkeypatch):
    monkeypatch.setattr(
        forms_repo, "get_form_by_slug", AsyncMock(return_value=dict(_FORM))
    )
    monkeypatch.setattr(forms_repo, "can_access_form", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_submit_form(slug="support", request=_form_request(), user=_NON_ADMIN)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_api_submit_form_forwards_super_admin_flag(monkeypatch):
    # Regression guard: the route must pass the super-admin flag through to
    # ``can_access_form`` so super admins can reach forms that have access rows.
    monkeypatch.setattr(
        forms_repo, "get_form_by_slug", AsyncMock(return_value=dict(_FORM))
    )
    can_access = AsyncMock(return_value=True)
    monkeypatch.setattr(forms_repo, "can_access_form", can_access)
    create_ticket = AsyncMock(return_value={"id": 555})
    monkeypatch.setattr("app.services.tickets.create_ticket", create_ticket)
    create_submission = AsyncMock(return_value={"id": 77})
    monkeypatch.setattr(forms_repo, "create_submission", create_submission)
    monkeypatch.setattr(forms_repo, "list_fields", AsyncMock(return_value=[]))
    monkeypatch.setattr(custom_forms, "log_info", lambda *a, **k: None)

    result = await forms_api.api_submit_form(
        slug="support", request=_form_request(), user=_SUPER_ADMIN
    )
    assert result.status_code == 201
    body = json.loads(result.body)
    assert body["status"] == "ok"
    assert body["ticket_id"] == 555
    assert can_access.await_args.kwargs.get("is_super_admin") is True
    create_ticket.assert_awaited_once()


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_runs_on_sqlite():
    migration = Path("migrations/485_custom_forms.sql").read_text(encoding="utf-8")
    adapted = db._adapt_sql_for_sqlite(migration)
    connection = sqlite3.connect(":memory:")
    # Prerequisite tables referenced by foreign keys, plus the legacy OpnForm
    # tables that the migration should drop.
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE forms (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE form_permissions (id INTEGER PRIMARY KEY)")

    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "custom_forms",
        "custom_form_fields",
        "custom_form_submissions",
        "custom_form_access",
    } <= tables
    # Legacy OpnForm tables are dropped.
    assert "forms" not in tables
    assert "form_permissions" not in tables

    # A form row round-trips with ``active`` defaulting to 1.
    connection.execute(
        "INSERT INTO custom_forms (slug, name) VALUES ('support', 'Support')"
    )
    assert connection.execute(
        "SELECT active FROM custom_forms WHERE slug = 'support'"
    ).fetchone() == (1,)

    # A submission links a form to its ticket.
    connection.execute(
        "INSERT INTO custom_form_submissions (form_id, ticket_id) VALUES (1, 555)"
    )
    assert connection.execute(
        "SELECT ticket_id FROM custom_form_submissions WHERE form_id = 1"
    ).fetchone() == (555,)


def test_migration_486_adds_config_column():
    base = Path("migrations/485_custom_forms.sql").read_text(encoding="utf-8")
    alter = Path("migrations/486_custom_form_fields_config.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE forms (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE form_permissions (id INTEGER PRIMARY KEY)")
    for migration in (base, alter):
        adapted = db._adapt_sql_for_sqlite(migration)
        for statement in db._split_sql_statements(adapted):
            connection.execute(statement)
    # The new JSON column exists on custom_form_fields.
    cols = {row[1] for row in connection.execute("PRAGMA table_info(custom_form_fields)")}
    assert "config" in cols


def _form_request():
    """A minimal fake ASGI request exposing ``form()``, ``client`` and ``headers``."""

    class _Form:
        def getlist(self, name):
            return []

        def get(self, key, default=None):
            return default

    class _Client:
        host = "1.2.3.4"

    class _Headers:
        def get(self, key, default=None):
            return default

    class _Request:
        client = _Client()
        headers = _Headers()

        def form(self):
            async def _coro():
                return _Form()

            return _coro()

    return _Request()