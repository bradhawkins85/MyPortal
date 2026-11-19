"""Rate any AI output 👍/👎 so MyPortal can report on and learn from it."""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.dependencies.auth import get_current_user
from app.repositories import ai_quality as quality_repo
from app.security.flash import _safe_redirect_target, local_redirect
from app.schemas.ai_quality import AiFeedbackRequest, AiFeedbackResponse
from app.services import ai_learning
from app.services import ai_quality_access as access
from app.services import audit as audit_service

router = APIRouter(prefix="/api/ai-quality", tags=["AI Quality"])

_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def safe_return_path(value: Any) -> str:
    """Only same-site paths; anything else (or anything with control characters) returns to ``/``."""
    return _safe_redirect_target(value if isinstance(value, str) else "", fallback="/")


async def submit_rating(
    user: Mapping[str, Any],
    payload: AiFeedbackRequest,
    *,
    request: Request | None = None,
) -> dict[str, Any]:
    """Validate, authorise and save one vote; shared by every feedback route."""
    if payload.reason and payload.reason not in quality_repo.REASONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown feedback reason")
    response = await quality_repo.get_response(payload.response_id)
    technician = await access.is_technician(user)
    if not response or not await access.can_rate(user, response, is_technician=technician):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI output not found")
    user_id = int(user["id"])
    try:
        await quality_repo.save_feedback(
            response_id=payload.response_id,
            user_id=user_id,
            rating=payload.rating,
            reason=payload.reason if payload.rating == "down" else None,
            comment=(payload.comment or "").strip() or None,
            is_technician=technician,
        )
    finally:
        ai_learning.invalidate_cache()
    await audit_service.record(
        action="ai_quality.feedback",
        request=request,
        user_id=user_id,
        entity_type="ai_output",
        entity_id=payload.response_id,
        metadata={"rating": payload.rating, "reason": payload.reason, "feature": response.get("feature")},
    )
    counts = await quality_repo.response_counts(payload.response_id)
    return {"response_id": payload.response_id, "rating": payload.rating, **counts}


@router.post(
    "/feedback",
    response_model=AiFeedbackResponse,
    summary="Rate an AI output",
    description=(
        "Record the signed-in user's 👍 (`up`) or 👎 (`down`) on an AI output, or clear it with `null`. "
        "Accepts JSON, or a form post with `return_to` (redirects back with 303) for pages without JavaScript. "
        "Returns 404 when the output does not exist or the user cannot see it."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": AiFeedbackRequest.model_json_schema()}},
        }
    },
)
async def rate_ai_output(request: Request, current_user: dict = Depends(get_current_user)):
    content_type = request.headers.get("content-type", "").lower()
    is_form = content_type.startswith(_FORM_CONTENT_TYPES)
    if is_form:
        form = await request.form()
        # Only plain text fields count; a file part is never a valid value.
        raw = {key: form.get(key) for key in ("response_id", "rating", "reason", "comment")}
        raw = {key: (value if isinstance(value, str) and value else None) for key, value in raw.items()}
    else:
        try:
            raw = await request.json()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected a JSON object")
    try:
        payload = AiFeedbackRequest.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.errors(include_url=False, include_input=False))
    result = await submit_rating(current_user, payload, request=request)
    if is_form:
        return local_redirect(safe_return_path(form.get("return_to")), status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse(result)
