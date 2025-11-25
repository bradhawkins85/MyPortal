from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.csrf import CSRFMiddleware


@dataclass
class _DummySession:
    csrf_token: str
    user_id: int | None = None


class _DummySessionManager:
    def __init__(self, token: str) -> None:
        self._session = _DummySession(csrf_token=token)

    async def load_session(self, request, allow_inactive: bool = False):  # noqa: D401 - FastAPI middleware signature
        return self._session


_dummy_manager = _DummySessionManager(token="test-token")

app = FastAPI()
app.add_middleware(CSRFMiddleware, manager=_dummy_manager)


@app.post("/upload")
async def upload_endpoint(
    name: str = Form(...),
    file: UploadFile = File(...),
    csrf_token: str | None = Form(default=None, alias="_csrf"),
) -> JSONResponse:  # pragma: no cover - exercised via tests
    content = await file.read()
    return JSONResponse({"name": name, "size": len(content)})


client = TestClient(app)


def test_multipart_post_without_csrf_is_rejected():
    response = client.post(
        "/upload",
        data={"name": "Example"},
        files={"file": ("sample.txt", b"data", "text/plain")},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token missing"}


def test_multipart_post_with_form_csrf_is_accepted():
    response = client.post(
        "/upload",
        data={"name": "Example", "_csrf": "test-token"},
        files={"file": ("sample.txt", b"data", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json() == {"name": "Example", "size": 4}


def test_exempt_path_allows_post_without_csrf():
    exempt_app = FastAPI()
    exempt_app.add_middleware(
        CSRFMiddleware,
        manager=_dummy_manager,
        exempt_paths=("/api/webhooks/smtp2go",),
    )

    @exempt_app.post("/api/webhooks/smtp2go/events")
    async def webhook_endpoint():
        return JSONResponse({"status": "ok"})

    exempt_client = TestClient(exempt_app)
    response = exempt_client.post(
        "/api/webhooks/smtp2go/events",
        json={"event": "delivered"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_authorization_header_skips_csrf_validation():
    token_app = FastAPI()
    token_app.add_middleware(CSRFMiddleware, manager=_dummy_manager)

    @token_app.post("/api/tickets")
    async def create_ticket():
        return JSONResponse({"status": "created"})

    token_client = TestClient(token_app)
    response = token_client.post("/api/tickets", headers={"Authorization": "Bearer abc123"})

    assert response.status_code == 200
    assert response.json() == {"status": "created"}


def test_api_key_header_skips_csrf_validation():
    key_app = FastAPI()
    key_app.add_middleware(CSRFMiddleware, manager=_dummy_manager)

    @key_app.post("/api/tickets")
    async def create_ticket_with_key():
        return JSONResponse({"status": "created"})

    key_client = TestClient(key_app)
    response = key_client.post("/api/tickets", headers={"x-api-key": "test-key"})

    assert response.status_code == 200
    assert response.json() == {"status": "created"}
