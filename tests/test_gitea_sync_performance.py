"""Regression checks for pooled downloads and bounded sync database work."""
import asyncio
import contextlib

import httpx
import pytest

from app.repositories import rmm as rmm_repo
from app.services import gitea


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings():
    return gitea.GiteaSettings("https://git.example.com", "https://git.example.com", "token", "msp", "scripts", "main", "rmm", True)


@pytest.mark.anyio
async def test_downloads_share_one_client_and_limit_concurrency(monkeypatch, settings):
    clients = []
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            assert request.headers["Authorization"] == "token token"
            assert request.url.params["ref"] == "main"
            return httpx.Response(200, content=request.url.path.encode())
        finally:
            active -= 1

    def client_factory(settings):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=gitea._headers(settings))
        clients.append(client)
        return client

    monkeypatch.setattr(gitea, "_client", client_factory)
    paths = [f"rmm/Common/script {i}.sh" for i in range(19)]
    downloaded = []
    async with contextlib.aclosing(gitea.file_batches(settings, iter(paths))) as batches:
        async for batch in batches:
            downloaded.extend(batch)
    assert [path for path, _ in downloaded] == paths
    assert all(raw.endswith(path.encode()) for path, raw in downloaded)
    assert peak == 8 and active == 0
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.anyio
async def test_failed_batch_finishes_requests_before_closing_client(monkeypatch, settings):
    finished = []

    async def handler(request):
        await asyncio.sleep(0)
        finished.append(request.url.path)
        return httpx.Response(403 if request.url.path.endswith('/0.sh') else 200, content=b'echo ok')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gitea, "_client", lambda settings: client)
    with pytest.raises(gitea.GiteaError, match="refused access"):
        async with contextlib.aclosing(gitea.file_batches(settings, [f"Common/{i}.sh" for i in range(19)])) as batches:
            async for batch in batches:
                pytest.fail("A failed batch must not be yielded")
    assert len(finished) == 8 and client.is_closed


@pytest.mark.anyio
async def test_empty_downloads_do_not_open_a_client(monkeypatch, settings):
    def unexpected_client(settings):
        pytest.fail("No downloads need no HTTP client")

    monkeypatch.setattr(gitea, "_client", unexpected_client)
    assert [batch async for batch in gitea.file_batches(settings, [])] == []


@pytest.mark.anyio
@pytest.mark.parametrize("update", [rmm_repo.touch_scripts, rmm_repo.deactivate_scripts])
async def test_sync_updates_database_in_bounded_batches(monkeypatch, update):
    calls = []

    class Database:
        async def execute(self, sql, params):
            calls.append((sql, params))

    monkeypatch.setattr(rmm_repo, "db", Database())
    paths = [f"Common/{i}.sh" for i in range(1001)]
    await update(iter(paths))
    assert [len(params) - 1 for _, params in calls] == [500, 500, 1]
    assert [path for _, params in calls for path in params[1:]] == paths
    assert all(sql.count('%s') == len(params) for sql, params in calls)
    calls.clear()
    await update([])
    assert calls == []
