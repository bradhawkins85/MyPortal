from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

from app.api.dependencies.auth import require_super_admin
from app.core.config import get_settings
from app.repositories import companies as company_repo
from app.services import tickets as tickets_service
from app.services import trello as trello_service
from app.services import webhook_monitor

router = APIRouter(prefix="/api/integration-modules/trello", tags=["Trello"])

# ---------------------------------------------------------------------------
# Webhook endpoint (Trello verification + event ingestion)
# ---------------------------------------------------------------------------

@router.head("/webhook", status_code=status.HTTP_200_OK)
@router.get("/webhook", status_code=status.HTTP_200_OK)
async def trello_webhook_verify(request: Request) -> JSONResponse:
    """Trello sends HEAD/GET requests to verify the callback URL is reachable.

    If a GET arrives carrying both ``x-trello-webhook`` (HMAC signature) and
    ``content-type: application/json``, log a warning: those headers are only
    present on real event POSTs, so seeing them on a GET means a 301/302
    redirect somewhere upstream is downgrading the POST method (the classic
    cause is the registered ``callbackURL`` being ``http://`` while the proxy
    redirects to ``https://``).
    """
    headers = request.headers
    if (
        headers.get("x-trello-webhook")
        and "application/json" in (headers.get("content-type") or "").lower()
    ):
        logger.warning(
            "Trello webhook GET carries POST-only headers (x-trello-webhook, "
            "content-type=application/json). This indicates a 301/302 redirect "
            "is downgrading Trello's POST to GET. Verify the registered "
            "callbackURL matches the public scheme/host (set PUBLIC_BASE_URL or "
            "configure the reverse proxy to forward X-Forwarded-Proto)."
        )
    return JSONResponse(content={}, status_code=200)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def trello_webhook_receive(request: Request) -> JSONResponse:
    """Receive Trello webhook events and dispatch to the appropriate handler.

    Supported action types:
    - ``createCard`` – create a new MyPortal ticket from the card.
    - ``commentCard`` – add a reply to the linked ticket (skips MyPortal-origin
      comments identified by the :data:`~app.services.trello.MYPORTAL_COMMENT_PREFIX`).
    - ``updateCard`` – add an internal note when a card's description changes.
    """
    source_url = str(request.url)
    request_headers = dict(request.headers)

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        await webhook_monitor.log_incoming_webhook(
            name="Trello Webhook - Invalid JSON",
            source_url=source_url,
            headers=request_headers,
            response_status=400,
            response_body="Invalid JSON payload",
            error_message="Invalid JSON payload",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    action: dict[str, Any] = payload.get("action") or {}
    action_type: str = str(action.get("type") or "").strip()
    data: dict[str, Any] = action.get("data") or {}

    board_data: dict[str, Any] = data.get("board") or {}
    board_id: str = str(board_data.get("id") or "").strip()

    card_data: dict[str, Any] = data.get("card") or {}
    card_id: str = str(card_data.get("id") or "").strip()

    if not board_id or not card_id:
        # Ignore events that lack board/card context (e.g. board-level events)
        await webhook_monitor.log_incoming_webhook(
            name=f"Trello Webhook - {action_type or 'unknown'} (ignored)",
            source_url=source_url,
            payload=payload,
            headers=request_headers,
            response_status=200,
            response_body="ignored",
        )
        return JSONResponse(content={"status": "ignored"})

    error_message: str | None = None
    try:
        if action_type == "createCard":
            await _handle_create_card(board_id, card_id, card_data, action)
        elif action_type == "commentCard":
            await _handle_comment_card(card_id, data, action)
        elif action_type == "updateCard":
            await _handle_update_card(card_id, data)
    except Exception as exc:
        error_message = str(exc)
        logger.error("Trello webhook handler error for {}: {}", action_type, exc)

    # Always return 200 to Trello regardless of internal errors.  A non-200
    # response would cause Trello to retry indefinitely.  Failures are
    # surfaced in the webhook monitor (logged as "failed" when error_message
    # is set) so they remain visible without triggering retries.
    await webhook_monitor.log_incoming_webhook(
        name=f"Trello Webhook - {action_type or 'unknown'}",
        source_url=source_url,
        payload=payload,
        headers=request_headers,
        response_status=200,
        response_body="ok" if not error_message else error_message,
        error_message=error_message,
    )

    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Admin: register a Trello webhook for a board
# ---------------------------------------------------------------------------

@router.post(
    "/boards/{board_id}/register-webhook",
    status_code=status.HTTP_200_OK,
)
async def register_trello_webhook(
    board_id: str,
    request: Request,
    _current_user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    """Register a Trello webhook for *board_id* pointing back to this server.

    The callback URL is derived from the current request's base URL so it works
    in both development and production environments.  The board must already be
    linked to a company (via the company's Trello board ID field) so that the
    per-company API credentials can be used.
    """
    board_id = board_id.strip()
    if not board_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="board_id is required",
        )

    company = await trello_service.get_company_for_board(board_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No company is linked to this board ID. "
                "Set the Trello board ID on the company first."
            ),
        )

    callback_url = _build_public_callback_url(request)
    logger.info(
        "Trello register_webhook: registering board {} with callback URL {}",
        board_id,
        callback_url,
    )
    result = await trello_service.register_webhook(board_id, callback_url, company=company)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Failed to register Trello webhook. "
                "Check that the Trello module is enabled and the company's "
                "API key and token are configured."
            ),
        )
    return {"status": "ok", "webhook": result, "callback_url": callback_url}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WEBHOOK_PATH = "/api/integration-modules/trello/webhook"


def _build_public_callback_url(request: Request) -> str:
    """Return the public HTTPS-aware callback URL for the Trello webhook.

    Resolution order (first match wins):

    1. The ``PUBLIC_BASE_URL`` setting, if configured. This is the most
       reliable source because it does not depend on per-request headers.
    2. ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` headers from the reverse
       proxy.
    3. If the request appears to come from behind a proxy (``X-Forwarded-For``
       is present) but no ``X-Forwarded-Proto`` was supplied, default the
       scheme to ``https``. Reverse proxies almost always terminate TLS, so
       inferring ``http`` from ``request.url.scheme`` (which reflects the
       hop between the proxy and uvicorn) would register a stale ``http://``
       callback. Trello would then follow the proxy's HTTP→HTTPS ``301``
       redirect and downgrade its event ``POST`` to a ``GET``, so MyPortal
       would never receive real webhook events.
    4. Fall back to ``request.base_url``.
    """

    settings = get_settings()
    public_base = (settings.public_base_url or "").strip().rstrip("/")
    if public_base:
        # Defensive: if the operator omitted the scheme, assume https.
        if "://" not in public_base:
            public_base = f"https://{public_base}"
        return f"{public_base}{_WEBHOOK_PATH}"

    def _first(value: str) -> str:
        # Forwarded headers may contain a comma-separated list; the
        # left-most value is the one closest to the original client.
        return value.split(",")[0].strip()

    forwarded_proto = _first(request.headers.get("x-forwarded-proto", ""))
    forwarded_host = _first(request.headers.get("x-forwarded-host", ""))
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()

    if forwarded_proto:
        scheme = forwarded_proto.lower()
    elif forwarded_for:
        # Behind a proxy but no proto header – default to https since proxies
        # almost always terminate TLS in front of uvicorn.
        scheme = "https"
    else:
        scheme = (request.url.scheme or "https").lower()

    if scheme not in ("http", "https"):
        # Defensive: reject unexpected schemes from spoofed headers.
        scheme = "https"

    host = forwarded_host or request.headers.get("host") or request.url.netloc
    # Strip any path/query that a malformed header might contain.
    host = host.split("/")[0].strip()
    if not host:
        # Last-resort fallback to the ASGI base URL.
        return f"{str(request.base_url).rstrip('/')}{_WEBHOOK_PATH}"

    return f"{scheme}://{host}{_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------

async def _handle_create_card(
    board_id: str,
    card_id: str,
    card_data: dict[str, Any],
    action: dict[str, Any],
) -> None:
    """Create a MyPortal ticket when a card is created on a linked board."""

    # Check if a ticket already exists for this card (idempotency)
    existing = await trello_service.find_ticket_for_card(card_id)
    if existing:
        logger.debug(
            "Trello createCard: ticket already exists for card {}", card_id
        )
        return

    # Resolve the company linked to this board
    company = await trello_service.get_company_for_board(board_id)
    company_id: int | None = int(company["id"]) if company else None

    card_name: str = str(card_data.get("name") or "").strip() or "(no title)"
    card_desc: str | None = str(card_data.get("desc") or "").strip() or None

    try:
        ticket = await tickets_service.create_ticket(
            subject=card_name,
            description=card_desc,
            requester_id=None,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category=None,
            module_slug=trello_service.TRELLO_MODULE_SLUG,
            external_reference=card_id,
            trigger_automations=True,
        )
    except Exception as exc:
        logger.error(
            "Trello createCard: failed to create ticket for card {}: {}",
            card_id,
            exc,
        )
        return

    ticket_id = ticket.get("id")
    ticket_number = ticket.get("ticket_number") or ticket_id
    logger.info(
        "Trello createCard: created ticket {} for card {}", ticket_id, card_id
    )

    # Post confirmation comment back on the Trello card (new requirement)
    await trello_service.post_ticket_created_comment(card_id, ticket_number, company=company)


async def _handle_comment_card(
    card_id: str,
    data: dict[str, Any],
    action: dict[str, Any],
) -> None:
    """Add a ticket reply when a comment is posted on a linked Trello card."""

    comment_text: str = str(data.get("text") or "").strip()
    if not comment_text:
        return

    # Skip comments that MyPortal itself posted (identified by prefix)
    if comment_text.startswith(trello_service.MYPORTAL_COMMENT_PREFIX):
        logger.debug(
            "Trello commentCard: skipping MyPortal-origin comment on card {}",
            card_id,
        )
        return

    ticket = await trello_service.find_ticket_for_card(card_id)
    if not ticket:
        logger.debug(
            "Trello commentCard: no ticket found for card {}; ignoring", card_id
        )
        return

    ticket_id: int = int(ticket["id"])
    member_creator: dict[str, Any] = action.get("memberCreator") or {}
    author_label = (
        str(member_creator.get("fullName") or member_creator.get("username") or "")
        .strip()
        or "Trello"
    )
    body = f"<p><strong>{author_label} (Trello):</strong> {comment_text}</p>"

    try:
        from app.repositories import tickets as tickets_repo
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=None,
            body=body,
            is_internal=False,
        )
        await tickets_service.emit_ticket_updated_event(ticket_id, actor_type="system")
        logger.info(
            "Trello commentCard: added reply to ticket {} from card {}",
            ticket_id,
            card_id,
        )
    except Exception as exc:
        logger.error(
            "Trello commentCard: failed to add reply to ticket {}: {}",
            ticket_id,
            exc,
        )


async def _handle_update_card(
    card_id: str,
    data: dict[str, Any],
) -> None:
    """Add an internal note when a card's description is updated."""

    old_data: dict[str, Any] = data.get("old") or {}
    if "desc" not in old_data:
        # The update is not a description change; ignore
        return

    new_desc: str = str((data.get("card") or {}).get("desc") or "").strip()
    old_desc: str = str(old_data.get("desc") or "").strip()
    if new_desc == old_desc:
        return

    ticket = await trello_service.find_ticket_for_card(card_id)
    if not ticket:
        logger.debug(
            "Trello updateCard: no ticket found for card {}; ignoring", card_id
        )
        return

    ticket_id: int = int(ticket["id"])
    body = (
        "<p><strong>Card description updated in Trello:</strong></p>"
        f"<p>{new_desc}</p>"
    )

    try:
        from app.repositories import tickets as tickets_repo
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=None,
            body=body,
            is_internal=True,
        )
        await tickets_service.emit_ticket_updated_event(ticket_id, actor_type="system")
        logger.info(
            "Trello updateCard: added internal note to ticket {} for card {}",
            ticket_id,
            card_id,
        )
    except Exception as exc:
        logger.error(
            "Trello updateCard: failed to add note to ticket {}: {}",
            ticket_id,
            exc,
        )
