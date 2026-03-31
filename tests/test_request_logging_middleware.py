from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.request_logger import RequestLoggingMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware, exempt_paths=("/static",))

    @app.get('/ok')
    async def ok(request: Request):
        return {'request_id': request.state.request_id}

    @app.get('/static/ping')
    async def static_ping(request: Request):
        return {'request_id': request.state.request_id}

    @app.get('/boom')
    async def boom():
        raise RuntimeError('boom')

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, 'request_id', None)
        return JSONResponse(
            {'detail': 'Internal server error', 'request_id': request_id},
            status_code=500,
            headers={'X-Request-ID': request_id or ''},
        )

    return app


def test_assigns_new_request_id_and_echoes_header() -> None:
    client = TestClient(_build_app())
    response = client.get('/ok')
    assert response.status_code == 200
    assert response.headers.get('X-Request-ID')
    assert response.json()['request_id'] == response.headers['X-Request-ID']


def test_reuses_incoming_request_id() -> None:
    client = TestClient(_build_app())
    response = client.get('/ok', headers={'X-Request-ID': 'req-1234'})
    assert response.status_code == 200
    assert response.headers['X-Request-ID'] == 'req-1234'
    assert response.json()['request_id'] == 'req-1234'


def test_sets_header_for_exempt_paths() -> None:
    client = TestClient(_build_app())
    response = client.get('/static/ping')
    assert response.status_code == 200
    assert response.headers.get('X-Request-ID')


def test_request_id_survives_exception_path() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get('/boom', headers={'X-Request-ID': 'req-explode'})
    assert response.status_code == 500
    assert response.headers['X-Request-ID'] == 'req-explode'
    assert response.json()['request_id'] == 'req-explode'
