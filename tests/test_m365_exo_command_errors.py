"""Regression tests for actionable Exchange Online command failures."""

from unittest.mock import patch

import pytest

from app.services.m365 import M365Error, _exo_invoke_command


@pytest.mark.anyio("asyncio")
async def test_exo_invoke_command_surfaces_json_error_message():
    class Response:
        status_code = 403
        text = '{"error":{"message":"The role assigned to application is not supported."}}'

        def json(self):
            return {
                "error": {
                    "message": "The role assigned to application is not supported."
                }
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    with patch("app.services.m365.httpx.AsyncClient", Client):
        with pytest.raises(M365Error) as exc_info:
            await _exo_invoke_command(
                "token",
                "tenant-id",
                "Set-AntiPhishPolicy",
                {"EnableSimilarDomainsSafetyTips": True},
            )

    assert exc_info.value.http_status == 403
    assert str(exc_info.value) == (
        "Exchange Online Set-AntiPhishPolicy failed (403): "
        "The role assigned to application is not supported."
    )


@pytest.mark.anyio("asyncio")
async def test_exo_invoke_command_sanitizes_and_bounds_plain_text_error():
    class Response:
        status_code = 400
        text = "invalid parameter\nfor request\t" + ("x" * 600)

        def json(self):
            raise ValueError("not JSON")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    with patch("app.services.m365.httpx.AsyncClient", Client):
        with pytest.raises(M365Error) as exc_info:
            await _exo_invoke_command("token", "tenant-id", "Set-AntiPhishPolicy")

    detail = str(exc_info.value).split(": ", 1)[1]
    assert "\n" not in detail
    assert "\t" not in detail
    assert len(detail) == 500
