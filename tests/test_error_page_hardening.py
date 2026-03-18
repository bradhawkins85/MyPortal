import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/broken", query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query_string.encode(),
        "headers": [(b"accept", b"text/html")],
    }
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_build_safe_error_path_filters_query_params():
    request = _make_request(query_string="token=secret&page=2&tab=details")

    result = main._build_safe_error_path(request)

    assert result == "/broken?page=2&tab=details"


@pytest.mark.anyio("asyncio")
async def test_render_error_page_hides_details_in_production_for_non_admin(monkeypatch):
    request = _make_request(query_string="password=123")
    captured_extra = {}

    async def fake_get_optional_user(_request):
        return ({"id": 12, "is_super_admin": False}, None)

    async def fake_build_portal_context(_request, _user, *, extra=None):
        captured_extra.update(extra or {})
        return {"request": request, **(extra or {})}

    monkeypatch.setattr(main, "_get_optional_user", fake_get_optional_user)
    monkeypatch.setattr(main, "_build_portal_context", fake_build_portal_context)
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(
        main.templates,
        "TemplateResponse",
        lambda *_args, **_kwargs: {"ok": True},
    )

    await main._render_error_page(
        request,
        status_code=500,
        error_reference="abc123ref",
        title="Something went wrong",
        message="Broken",
        detail="sensitive stack trace",
    )

    assert captured_extra["error_detail"] is None
    assert captured_extra["error_path"] == "/broken"
    assert captured_extra["error_reference"] == "abc123ref"


@pytest.mark.anyio("asyncio")
async def test_handle_http_exception_logs_reference(monkeypatch):
    request = _make_request(path="/missing", query_string="token=secret&section=overview")
    captured = {}

    async def fake_render_error_page(req, **kwargs):
        captured["render"] = {"request": req, **kwargs}

        class FakeResponse:
            def __init__(self):
                self.headers = {}

        return FakeResponse()

    monkeypatch.setattr(main, "_generate_error_reference", lambda: "ref123456789")
    monkeypatch.setattr(main, "_render_error_page", fake_render_error_page)
    monkeypatch.setattr(main, "log_info", lambda message, **kwargs: captured.update({"log": (message, kwargs)}))

    await main.handle_http_exception(request, HTTPException(status_code=404, detail="Not found"))

    assert captured["render"]["error_reference"] == "ref123456789"
    assert captured["log"][1]["error_reference"] == "ref123456789"
    assert captured["log"][1]["request_path"] == "/missing?section=overview"


@pytest.mark.anyio("asyncio")
async def test_handle_unexpected_exception_logs_reference(monkeypatch):
    request = _make_request(path="/boom", query_string="tab=summary&secret=1")
    captured = {}

    async def fake_render_error_page(req, **kwargs):
        captured["render"] = {"request": req, **kwargs}
        return {"ok": True}

    monkeypatch.setattr(main, "_generate_error_reference", lambda: "boomref123456")
    monkeypatch.setattr(main, "_render_error_page", fake_render_error_page)
    monkeypatch.setattr(main, "log_error", lambda message, **kwargs: captured.update({"log": (message, kwargs)}))

    response = await main.handle_unexpected_exception(request, RuntimeError("boom"))

    assert response == {"ok": True}
    assert captured["render"]["error_reference"] == "boomref123456"
    assert captured["log"][1]["error_reference"] == "boomref123456"
    assert captured["log"][1]["request_path"] == "/boom?tab=summary"
