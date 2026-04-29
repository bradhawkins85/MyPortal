"""Tests for trello.register_webhook conflict-resolution logic."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import trello as trello_service


COMPANY = {"id": 1, "trello_api_key": "key123", "trello_token": "tok456"}
BOARD_ID = "board1"
HTTPS_URL = "https://example.com/api/integration-modules/trello/webhook"
HTTP_URL = "http://example.com/api/integration-modules/trello/webhook"


def _http_error(status_code: int, text: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.trello.com/1/webhooks")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError(text, request=request, response=response)


@pytest.fixture(autouse=True)
def mock_trello_module_enabled(monkeypatch):
    monkeypatch.setattr(
        trello_service,
        "_get_module_enabled",
        AsyncMock(return_value=True),
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_webhook_success():
    """Happy path: Trello accepts the registration."""
    created = {"id": "wh1", "idModel": BOARD_ID, "callbackURL": HTTPS_URL}

    with patch("app.services.trello.httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = created
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await trello_service.register_webhook(
            BOARD_ID, HTTPS_URL, company=COMPANY
        )

    assert result == created


# ---------------------------------------------------------------------------
# 400 "already exists" – same callback URL (idempotent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_webhook_already_exists_same_url():
    """If the webhook already points to the new URL, return the existing record."""
    existing_hook = {"id": "wh1", "idModel": BOARD_ID, "callbackURL": HTTPS_URL}

    with (
        patch(
            "app.services.trello.httpx.AsyncClient"
        ) as mock_client_cls,
        patch(
            "app.services.trello.list_webhooks",
            AsyncMock(return_value=[existing_hook]),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=_http_error(400, "A webhook with that callback, model, and token already exists")
        )
        mock_client_cls.return_value = mock_client

        result = await trello_service.register_webhook(
            BOARD_ID, HTTPS_URL, company=COMPANY
        )

    assert result == existing_hook


# ---------------------------------------------------------------------------
# 400 "already exists" – stale callback URL (HTTP → HTTPS migration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_webhook_replaces_stale_http_webhook():
    """After HTTP→HTTPS migration, the old http:// webhook is replaced."""
    stale_hook = {"id": "wh_old", "idModel": BOARD_ID, "callbackURL": HTTP_URL}
    new_hook = {"id": "wh_new", "idModel": BOARD_ID, "callbackURL": HTTPS_URL}

    post_responses = [
        _http_error(400, "A webhook with that callback, model, and token already exists"),
        MagicMock(raise_for_status=MagicMock(), json=MagicMock(return_value=new_hook)),
    ]

    with (
        patch("app.services.trello.httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.services.trello.list_webhooks",
            AsyncMock(return_value=[stale_hook]),
        ),
        patch(
            "app.services.trello.delete_webhook",
            AsyncMock(return_value=True),
        ) as mock_delete,
    ):
        call_count = 0

        async def _post(*args, **kwargs):
            nonlocal call_count
            r = post_responses[call_count]
            call_count += 1
            if isinstance(r, Exception):
                raise r
            return r

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _post
        mock_client_cls.return_value = mock_client

        result = await trello_service.register_webhook(
            BOARD_ID, HTTPS_URL, company=COMPANY
        )

    assert result == new_hook
    mock_delete.assert_awaited_once_with("wh_old", "key123", "tok456")


# ---------------------------------------------------------------------------
# 400 "already exists" – no matching webhook found in list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_webhook_already_exists_no_match_returns_none():
    """If the conflicting webhook cannot be found in the list, return None."""
    unrelated_hook = {"id": "wh_other", "idModel": "other_board", "callbackURL": HTTP_URL}

    with (
        patch("app.services.trello.httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.services.trello.list_webhooks",
            AsyncMock(return_value=[unrelated_hook]),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=_http_error(400, "A webhook with that callback, model, and token already exists")
        )
        mock_client_cls.return_value = mock_client

        result = await trello_service.register_webhook(
            BOARD_ID, HTTPS_URL, company=COMPANY
        )

    assert result is None


# ---------------------------------------------------------------------------
# Other 4xx errors are still propagated as failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_webhook_other_http_error_returns_none():
    """Non-400 HTTP errors (e.g. 401 Unauthorized) still return None."""
    with patch("app.services.trello.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=_http_error(401, "invalid key")
        )
        mock_client_cls.return_value = mock_client

        result = await trello_service.register_webhook(
            BOARD_ID, HTTPS_URL, company=COMPANY
        )

    assert result is None
