from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData, UploadFile

from app.api.routes import forms as forms_api
from app.services.custom_forms import FormValidationError

ADMIN = {"id": 9, "is_super_admin": True}
FORM = {"id": 1, "slug": "support", "name": "Support", "active": True}


def request(data, *, html=False):
    return SimpleNamespace(
        form=AsyncMock(return_value=FormData(data)),
        headers={"accept": "text/html" if html else "application/json"},
        client=None,
    )


@pytest.mark.asyncio
async def test_page_save_requires_super_admin_before_reading_data():
    req = request({"action": "create_form", "name": "Test"})
    with pytest.raises(HTTPException) as exc:
        await forms_api.save_custom_form_page(req, {"id": 1, "is_super_admin": False})
    assert exc.value.status_code == 403
    req.form.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_create_opens_new_builder(monkeypatch):
    create = AsyncMock(return_value={**FORM, "id": 42})
    monkeypatch.setattr(forms_api, "api_create_form", create)
    response = await forms_api.save_custom_form_page(request({"action": "create_form", "name": " Support "}), ADMIN)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/custom-forms?form_id=42"
    assert create.await_args.kwargs["payload"].name == "Support"


@pytest.mark.asyncio
async def test_page_validation_failure_does_not_create_form(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(forms_api, "api_create_form", create)
    response = await forms_api.save_custom_form_page(request({"action": "create_form", "name": "  "}), ADMIN)
    assert response.status_code == 303
    assert "_flash=" in response.headers["set-cookie"]
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field_id", ["", "10"])
async def test_page_field_save_uses_validated_api(monkeypatch, field_id):
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=FORM))
    create = AsyncMock()
    update = AsyncMock()
    monkeypatch.setattr(forms_api, "api_create_field", create)
    monkeypatch.setattr(forms_api, "api_update_field", update)
    response = await forms_api.save_custom_form_page(request({
        "action": "save_field", "form_id": "1", "field_id": field_id,
        "label": " Choose a team ", "field_type": "select", "required": "1",
        "options": " Operations \n\nSupport", "help_text": "Who can help?",
    }), ADMIN)
    assert response.status_code == 303
    args = (update if field_id else create).await_args.args
    payload = args[2] if field_id else args[1]
    assert payload.options == ["Operations", "Support"]
    assert payload.required is True
    assert payload.label == "Choose a team"
    assert response.headers["location"] == "/admin/custom-forms?form_id=1"


@pytest.mark.asyncio
async def test_page_reorder_matches_api_contract(monkeypatch):
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_api.forms_repo, "list_fields", AsyncMock(return_value=[{"id": 10}, {"id": 11}]))
    reorder = AsyncMock()
    monkeypatch.setattr(forms_api, "api_reorder_fields", reorder)
    await forms_api.save_custom_form_page(request({"action": "move_field", "form_id": "1", "field_id": "11", "direction": "up"}), ADMIN)
    assert reorder.await_args.args[1].field_ids == [11, 10]


@pytest.mark.asyncio
@pytest.mark.parametrize("action, expected", [("add_access", [4, 9]), ("remove_access", [])])
async def test_page_access_preserves_existing_selection(monkeypatch, action, expected):
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_api.forms_repo, "list_access_user_ids", AsyncMock(return_value=[9]))
    save = AsyncMock()
    monkeypatch.setattr(forms_api, "api_set_access", save)
    await forms_api.save_custom_form_page(request({"action": action, "form_id": "1", "user_id": "4" if action == "add_access" else "9"}), ADMIN)
    assert save.await_args.args[1].user_ids == expected


@pytest.mark.asyncio
async def test_page_actions_cannot_mutate_missing_form(monkeypatch):
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=None))
    delete = AsyncMock()
    monkeypatch.setattr(forms_api, "api_delete_form", delete)
    with pytest.raises(HTTPException) as exc:
        await forms_api.save_custom_form_page(request({"action": "delete_form", "form_id": "55"}), ADMIN)
    assert exc.value.status_code == 404
    delete.assert_not_awaited()


@pytest.fixture
def submission(monkeypatch):
    fields = [
        {"id": 10, "field_type": "text"}, {"id": 11, "field_type": "multiselect"},
        {"id": 12, "field_type": "file"}, {"id": 13, "field_type": "checkbox"},
    ]
    monkeypatch.setattr(forms_api.forms_repo, "get_form_by_slug", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_api.forms_repo, "list_fields", AsyncMock(return_value=fields))
    monkeypatch.setattr(forms_api.forms_repo, "can_access_form", AsyncMock(return_value=True))
    submit = AsyncMock(return_value={"ticket_id": 77, "submission_id": 3, "attachments": []})
    monkeypatch.setattr(forms_api.custom_forms, "submit_form", submit)
    return submit


@pytest.mark.asyncio
async def test_public_html_submission_handles_choices_and_blank_upload(submission):
    data = [("field_10", "My request"), ("field_11", "Operations"), ("field_11", "Support"),
            ("field_13", "1"), ("file_12", UploadFile(filename="", file=None))]
    response = await forms_api.api_submit_form("support", request(data, html=True), ADMIN)
    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/77"
    assert submission.await_args.kwargs["values"] == {10: "My request", 11: ["Operations", "Support"], 12: "", 13: "1"}
    assert submission.await_args.kwargs["files"] == {12: []}


@pytest.mark.asyncio
async def test_public_json_submission_keeps_contract_and_named_upload(submission):
    upload = UploadFile(filename="screenshot.png", file=None)
    response = await forms_api.api_submit_form("support", request({"form_data": '{"10": "Help", "11": ["Support"]}', "file_12": upload}), ADMIN)
    assert response.status_code == 201
    assert submission.await_args.kwargs["values"] == {10: "Help", 11: ["Support"]}
    assert submission.await_args.kwargs["files"] == {12: [upload]}


@pytest.mark.asyncio
async def test_html_validation_renders_entered_answers(submission, monkeypatch):
    import app.main as main_module
    submission.side_effect = FormValidationError(["Choose at least one option."])
    render = AsyncMock(return_value=HTMLResponse("Please check your answers"))
    monkeypatch.setattr(main_module, "_render_template", render)
    response = await forms_api.api_submit_form("support", request({"field_10": "Keep this answer"}, html=True), ADMIN)
    assert response.status_code == 400
    assert render.await_args.kwargs["extra"]["submitted_values"][10] == "Keep this answer"
    assert render.await_args.kwargs["extra"]["errors"] == ["Choose at least one option."]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_clear", [False, True])
async def test_field_optional_text_can_be_cleared_without_changing_omitted_fields(monkeypatch, explicit_clear):
    from app.schemas.forms import FormFieldUpdate
    existing = {"id": 10, "form_id": 1, "field_type": "text", "label": "Question", "required": False,
                "placeholder": "Old placeholder", "help_text": "Old help", "default_value": "Old default", "options": []}
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_api.forms_repo, "get_field", AsyncMock(return_value=existing))
    update = AsyncMock(return_value=existing)
    monkeypatch.setattr(forms_api.forms_repo, "update_field", update)
    payload = FormFieldUpdate(placeholder=None, help_text=None, default_value=None) if explicit_clear else FormFieldUpdate(label="New question")
    await forms_api.api_update_field(1, 10, payload, ADMIN)
    for name in ["placeholder", "help_text", "default_value"]:
        assert update.await_args.kwargs[name] == (None if explicit_clear else existing[name])


@pytest.mark.asyncio
async def test_form_description_can_be_cleared(monkeypatch):
    from app.schemas.forms import FormUpdate
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value={**FORM, "description": "Old description"}))
    update = AsyncMock(return_value=FORM)
    monkeypatch.setattr(forms_api.forms_repo, "update_form", update)
    await forms_api.api_update_form(1, FormUpdate(description=None), ADMIN)
    assert update.await_args.kwargs["description"] is None


@pytest.mark.asyncio
async def test_html_submission_preserves_matrix_and_multiple_audio_clips(submission, monkeypatch):
    monkeypatch.setattr(forms_api.forms_repo, "list_fields", AsyncMock(return_value=[
        {"id": 10, "field_type": "matrix", "config": {"rows": ["Support", "Hardware"], "columns": ["Good", "Bad"]}},
        {"id": 11, "field_type": "audio", "config": {"max_clips": 2}},
    ]))
    clips = [UploadFile(filename="one.wav", file=None), UploadFile(filename="two.wav", file=None)]
    data = [("matrix_10_0", "Good"), ("matrix_10_1", "Bad"), ("file_11", clips[0]), ("file_11", clips[1])]
    response = await forms_api.api_submit_form("support", request(data, html=True), ADMIN)
    assert response.status_code == 303
    assert submission.await_args.kwargs["values"][10] == {"Support": "Good", "Hardware": "Bad"}
    assert submission.await_args.kwargs["files"][11] == clips


@pytest.mark.asyncio
async def test_html_signature_upload_is_encoded_for_existing_validation(submission, monkeypatch):
    from tempfile import SpooledTemporaryFile
    from starlette.datastructures import Headers
    monkeypatch.setattr(forms_api.forms_repo, "list_fields", AsyncMock(return_value=[{"id": 10, "field_type": "signature"}]))
    image_file = SpooledTemporaryFile()
    image_file.write(b"example")
    image_file.seek(0)
    image = UploadFile(filename="signature.png", file=image_file, headers=Headers({"content-type": "image/png"}))
    await forms_api.api_submit_form("support", request({"signature_10": image}, html=True), ADMIN)
    assert submission.await_args.kwargs["values"][10] == "data:image/png;base64,ZXhhbXBsZQ=="


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type, extra, expected", [
    ("matrix", {"matrix_rows": "A\nB", "matrix_columns": "Yes\nNo"}, {"rows": ["A", "B"], "columns": ["Yes", "No"]}),
    ("rating", {"rating_max": "7"}, {"max": 7}),
    ("audio", {"audio_max": "3"}, {"max_clips": 3}),
])
async def test_html_field_configuration_survives_save(monkeypatch, field_type, extra, expected):
    monkeypatch.setattr(forms_api.forms_repo, "get_form", AsyncMock(return_value=FORM))
    create = AsyncMock()
    monkeypatch.setattr(forms_api, "api_create_field", create)
    await forms_api.save_custom_form_page(request({"action": "save_field", "form_id": "1", "field_type": field_type, "label": "Question", **extra}), ADMIN)
    assert create.await_args.args[1].config == expected
