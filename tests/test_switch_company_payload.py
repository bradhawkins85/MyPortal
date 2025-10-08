import json
from collections.abc import Mapping

import pytest
from starlette.requests import Request

from app.main import _extract_switch_company_payload, _first_non_blank


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _build_request(
    *,
    body: bytes = b"",
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    query_string: str = "",
) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "method": method,
        "path": "/switch-company",
        "raw_path": b"/switch-company",
        "query_string": query_string.encode("latin-1"),
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    body_sent = False

    async def receive() -> dict[str, object]:
        nonlocal body_sent
        if body_sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        body_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.mark.anyio("asyncio")
async def test_extracts_json_payload_without_modification() -> None:
    request = _build_request(
        body=json.dumps({"companyId": 42, "returnUrl": "/staff"}).encode("utf-8"),
        headers={"content-type": "application/json"},
    )

    data = await _extract_switch_company_payload(request)

    assert data == {"companyId": 42, "returnUrl": "/staff"}


@pytest.mark.anyio("asyncio")
async def test_extracts_form_payload_even_after_prior_reads() -> None:
    body = "companyId=7&returnUrl=%2Fshop&_csrf=test-token".encode("utf-8")
    request = _build_request(
        body=body,
        headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
    )

    # Simulate middleware already reading the form
    await request.form()

    data = await _extract_switch_company_payload(request)

    assert data["companyId"] == "7"
    assert data["returnUrl"] == "/shop"
    assert data["_csrf"] == "test-token"


@pytest.mark.anyio("asyncio")
async def test_extracts_form_payload_when_parser_missing() -> None:
    body = "companyId=11&returnUrl=%2Fstaff".encode("utf-8")
    request = _build_request(
        body=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    async def failing_form():  # type: ignore[return-value]
        raise RuntimeError("parser unavailable")

    request.form = failing_form  # type: ignore[assignment]

    data = await _extract_switch_company_payload(request)

    assert data["companyId"] == "11"
    assert data["returnUrl"] == "/staff"


@pytest.mark.anyio("asyncio")
async def test_extracts_json_payload_when_header_missing() -> None:
    request = _build_request(
        body=json.dumps({"companyId": 21, "returnUrl": "/dashboard"}).encode("utf-8"),
    )

    data = await _extract_switch_company_payload(request)

    assert data["companyId"] == 21
    assert data["returnUrl"] == "/dashboard"


def test_first_non_blank_prioritises_non_empty_values() -> None:
    body_data = {"companyId": "  ", "company_id": None}
    query_params = {"company_id": "99", "returnUrl": "/dashboard"}

    company_id = _first_non_blank(("companyId", "company_id"), body_data, query_params)
    return_url = _first_non_blank(("returnUrl", "return_url"), body_data, query_params)

    assert company_id == "99"
    assert return_url == "/dashboard"


def test_first_non_blank_accepts_non_string_values() -> None:
    source = {"companyId": 123}

    assert _first_non_blank(("companyId",), source) == 123
