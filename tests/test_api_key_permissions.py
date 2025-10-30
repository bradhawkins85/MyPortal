import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import api_keys as api_key_dependency
from app.api.dependencies import database as database_dependencies
from app.repositories import api_keys as api_key_repo


@pytest.fixture
def test_app(monkeypatch):
    app = FastAPI()

    @app.api_route("/protected", methods=["GET", "POST"])
    async def protected(_: dict = Depends(api_key_dependency.require_api_key)):
        return {"status": "ok"}

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def usage_calls(monkeypatch):
    calls: list[tuple[int, str]] = []

    async def record(api_key_id: int, ip_address: str) -> None:
        calls.append((api_key_id, ip_address))

    monkeypatch.setattr(api_key_repo, "record_api_key_usage", record)
    return calls


def test_missing_header_returns_unauthorised(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        raise AssertionError("should not be called")

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert usage_calls == []


def test_invalid_key_returns_forbidden(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return None

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get("/protected", headers={"x-api-key": "invalid"})

    assert response.status_code == 403
    assert usage_calls == []


def test_permission_denied_when_method_not_allowed(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {
            "id": 1,
            "permissions": [{"path": "/protected", "methods": ["GET"]}],
        }

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.post("/protected", headers={"x-api-key": "key"})

    assert response.status_code == 403
    assert usage_calls == []


def test_permission_denied_when_path_not_allowed(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {
            "id": 2,
            "permissions": [{"path": "/other", "methods": ["GET"]}],
        }

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get("/protected", headers={"x-api-key": "key"})

    assert response.status_code == 403
    assert usage_calls == []


def test_access_allowed_when_permission_matches(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {
            "id": 3,
            "permissions": [{"path": "/protected", "methods": ["GET"]}],
        }

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get("/protected", headers={"x-api-key": "key"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert usage_calls == [(3, "testclient")]


def test_unrestricted_keys_allow_all_routes(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {"id": 4, "permissions": []}

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.post("/protected", headers={"x-api-key": "key"})

    assert response.status_code == 200
    assert usage_calls == [(4, "testclient")]


def test_ip_restriction_blocks_unlisted_address(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {
            "id": 5,
            "permissions": [],
            "ip_restrictions": [{"cidr": "203.0.113.0/24"}],
        }

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get(
            "/protected",
            headers={"x-api-key": "key", "x-forwarded-for": "198.51.100.10"},
        )

    assert response.status_code == 403
    assert usage_calls == []


def test_ip_restriction_allows_listed_address(test_app, usage_calls, monkeypatch):
    async def fake_get_api_key_record(_: str):
        return {
            "id": 6,
            "permissions": [],
            "ip_restrictions": [{"cidr": "203.0.113.0/24"}],
        }

    monkeypatch.setattr(api_key_repo, "get_api_key_record", fake_get_api_key_record)

    with TestClient(test_app) as client:
        response = client.get(
            "/protected",
            headers={"x-api-key": "key", "x-forwarded-for": "203.0.113.9"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert usage_calls == [(6, "203.0.113.9")]
