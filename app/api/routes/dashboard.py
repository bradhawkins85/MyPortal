"""HTTP API for the customisable Dashboard.

Endpoints (all under ``/api/dashboard``):

* ``GET /catalogue`` — cards the current user is allowed to add.
* ``GET /layout`` — the user's saved layout (or generated default).
* ``PUT /layout`` — replaces the saved layout (filtered to allowed cards).
* ``POST /layout/reset`` — deletes the saved layout (defaults will apply).
* ``GET /cards/{card_id}`` — a single card's payload, used for client-side
  refresh and "add card" preview.

All endpoints require an authenticated session. Write endpoints additionally
go through the global :class:`CSRFMiddleware` automatically.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.api.dependencies.auth import get_current_user
from app.services import dashboard_cards

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _user_id(user: dict) -> int:
    try:
        value = int(user.get("id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user") from exc
    if value <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return value


async def _allowed_descriptors(request: Request, user: dict):
    ctx = await dashboard_cards.build_card_context(request, user)
    descriptors = await dashboard_cards.list_allowed_cards(ctx)
    return ctx, descriptors


@router.get("/catalogue")
async def get_catalogue(request: Request, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    _user_id(current_user)
    _, descriptors = await _allowed_descriptors(request, current_user)
    return {
        "items": [dashboard_cards._serialise_descriptor(d) for d in descriptors],
        "grid_columns": dashboard_cards.GRID_COLUMNS,
    }


@router.get("/layout")
async def get_layout(request: Request, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    user_id = _user_id(current_user)
    _, descriptors = await _allowed_descriptors(request, current_user)
    allowed_ids = {d.id for d in descriptors}
    saved = await dashboard_cards.load_layout(user_id)
    if saved:
        layout = [entry for entry in saved if entry["id"] in allowed_ids]
    else:
        layout = dashboard_cards.default_layout(allowed_ids)
    return {
        "cards": layout,
        "grid_columns": dashboard_cards.GRID_COLUMNS,
        "is_default": not saved,
    }


@router.put("/layout")
async def put_layout(
    request: Request,
    payload: Any = Body(...),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    user_id = _user_id(current_user)
    cards = payload
    if isinstance(payload, dict):
        cards = payload.get("cards")
    if not isinstance(cards, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cards must be a list")
    _, descriptors = await _allowed_descriptors(request, current_user)
    allowed_ids = {d.id for d in descriptors}
    try:
        sanitised = await dashboard_cards.save_layout(user_id, cards, allowed_ids=allowed_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"cards": sanitised, "grid_columns": dashboard_cards.GRID_COLUMNS}


@router.post("/layout/reset")
async def reset_layout(
    request: Request, current_user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    user_id = _user_id(current_user)
    await dashboard_cards.reset_layout(user_id)
    _, descriptors = await _allowed_descriptors(request, current_user)
    layout = dashboard_cards.default_layout({d.id for d in descriptors})
    return {"cards": layout, "grid_columns": dashboard_cards.GRID_COLUMNS, "is_default": True}


@router.get("/cards/{card_id}")
async def get_card_payload(
    card_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    descriptor = dashboard_cards.get_card(card_id)
    if descriptor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown card")
    ctx, descriptors = await _allowed_descriptors(request, current_user)
    if descriptor not in descriptors:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Card not available")
    payload = await dashboard_cards.build_card_payload(descriptor, ctx)
    return {
        "descriptor": dashboard_cards._serialise_descriptor(descriptor),
        "payload": payload,
    }
