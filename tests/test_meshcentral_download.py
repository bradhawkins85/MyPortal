"""Authenticated installer downloads never expose login tokens to endpoints/errors."""
import httpx
import pytest

from app.services import meshcentral


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def mock_http(monkeypatch, handler):
    client = httpx.AsyncClient
    def session_handler(request):
        if request.url.path.endswith('/'):
            assert request.url.params['login']
            return httpx.Response(200, headers={'set-cookie': 'mesh-session=signed-in; Path=/; Secure; HttpOnly'})
        assert request.headers['cookie'] == 'mesh-session=signed-in'
        return handler(request)
    monkeypatch.setattr(meshcentral.httpx, 'AsyncClient',
                        lambda **kwargs: client(transport=httpx.MockTransport(session_handler), **kwargs))


@pytest.mark.anyio
async def test_download_uses_auth_and_encodes_group(monkeypatch):
    def respond(request):
        assert request.url.path == '/domain/meshagents'
        assert dict(request.url.params) == {'id': '4', 'meshid': 'group$@+&', 'installflags': '2'}
        return httpx.Response(200, content=b'MZ\x00\xffinstaller')
    mock_http(monkeypatch, respond)
    assert await meshcentral.download_installer('https://mesh.test/domain/', 'secret@$', 'group$@+&', 4) == b'MZ\x00\xffinstaller'


@pytest.mark.anyio
@pytest.mark.parametrize('status,headers,body', [
    (401, {}, b''), (302, {'location': '/login'}, b''),
    (200, {'content-type': 'text/html'}, b'login'),
    (200, {}, b'<!doctype html>'), (200, {}, b''),
])
async def test_download_rejects_auth_failures_and_invalid_files(monkeypatch, status, headers, body):
    mock_http(monkeypatch, lambda request: httpx.Response(status, headers=headers, content=body))
    with pytest.raises(meshcentral.MeshCentralError) as error:
        await meshcentral.download_installer('https://mesh.test', 'secret-token', 'group', 4)
    assert 'secret-token' not in str(error.value)


@pytest.mark.anyio
async def test_network_error_does_not_include_token(monkeypatch):
    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)
    mock_http(monkeypatch, fail)
    with pytest.raises(meshcentral.MeshCentralError) as error:
        await meshcentral.download_installer('https://mesh.test', 'secret-token', 'group', 4)
    assert 'secret-token' not in str(error.value)


@pytest.mark.anyio
async def test_download_enforces_size_limit(monkeypatch):
    from app.services import rmm_script_files
    monkeypatch.setattr(rmm_script_files, 'MAX_FILE_BYTES', 3)
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b'MZ123'))
    with pytest.raises(meshcentral.MeshCentralError, match='50 MB'):
        await meshcentral.download_installer('https://mesh.test', 'secret', 'group', 4)


@pytest.mark.anyio
async def test_missing_login_session_is_rejected(monkeypatch):
    client = httpx.AsyncClient
    def respond(request):
        assert request.url.path == '/'
        return httpx.Response(200, content=b'<html>Sign in</html>')
    monkeypatch.setattr(meshcentral.httpx, 'AsyncClient',
                        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(meshcentral.MeshCentralError, match='sign in'):
        await meshcentral.download_installer('https://mesh.test', 'secret-token', 'group', 4)
