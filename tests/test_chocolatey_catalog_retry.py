"""Scheduled catalog refresh recovery, with no network or database writes."""
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import chocolatey_catalog as catalog


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def feed(package=None):
    entry = (f'<entry><m:properties><d:Id>{package}</d:Id><d:Title>{package}</d:Title>'
             '<d:Version>1.0</d:Version><d:IsPrerelease>false</d:IsPrerelease>'
             '</m:properties></entry>') if package else ''
    return ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">' + entry + '</feed>')


@pytest.fixture
def refresh_mocks(monkeypatch):
    sleep = AsyncMock()
    replace = AsyncMock(return_value={'stored': 2, 'removed': 0})
    monkeypatch.setattr(catalog.asyncio, 'sleep', sleep)
    monkeypatch.setattr(catalog.catalog_repo, 'replace_all', replace)
    catalog._CACHE.clear()
    yield sleep, replace
    catalog._CACHE.clear()


def transport(monkeypatch, handler):
    monkeypatch.setattr(catalog, 'monitored_client', lambda *args, **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [429, 500, 502, 503, 504, 'timeout', 'connection'])
async def test_refresh_recovers_same_page(monkeypatch, refresh_mocks, failure):
    sleep, replace = refresh_mocks
    offsets = []

    def handle(request):
        offset = request.url.params['$skip']
        offsets.append(offset)
        if len(offsets) == 2:
            if failure == 'timeout':
                raise httpx.ReadTimeout('upstream timeout', request=request)
            if failure == 'connection':
                raise httpx.ConnectError('connection failed', request=request)
            return httpx.Response(failure)
        return httpx.Response(200, text=feed({'0': 'alpha', '1': 'beta'}.get(offset)))

    transport(monkeypatch, handle)
    catalog._CACHE[('old', False)] = (0, [])
    result = await catalog.refresh()
    assert offsets == ['0', '1', '1', '2']
    assert [p['package_id'] for p in replace.await_args.args[0]] == ['alpha', 'beta']
    assert result['pages'] == 3
    assert result['stored'] == 2
    assert 2.0 in [call.args[0] for call in sleep.await_args_list]
    assert not catalog._CACHE


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [504, 'timeout'])
async def test_exhausted_retries_preserve_stored_list_and_cache(monkeypatch, refresh_mocks, failure):
    sleep, replace = refresh_mocks
    offsets = []

    def handle(request):
        offset = request.url.params['$skip']
        offsets.append(offset)
        if offset == '0':
            return httpx.Response(200, text=feed('alpha'))
        if failure == 'timeout':
            raise httpx.ReadTimeout('upstream timeout', request=request)
        return httpx.Response(failure)

    transport(monkeypatch, handle)
    catalog._CACHE[('old', False)] = (0, [])
    with pytest.raises(catalog.CatalogUnavailable, match='stored app list was kept'):
        await catalog.refresh()
    assert offsets == ['0', '1', '1', '1', '1']
    assert [call.args[0] for call in sleep.await_args_list] == [catalog.PAGE_DELAY, 2, 4, 8]
    replace.assert_not_awaited()
    assert ('old', False) in catalog._CACHE


@pytest.mark.anyio
@pytest.mark.parametrize('status', [400, 401, 403, 404])
async def test_permanent_http_errors_are_not_retried(monkeypatch, refresh_mocks, status):
    sleep, replace = refresh_mocks
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status)
    transport(monkeypatch, handle)
    with pytest.raises(catalog.CatalogUnavailable, match=f'HTTP {status}'):
        await catalog.refresh()
    assert len(calls) == 1
    sleep.assert_not_awaited()
    replace.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('retry_after,expected', [('12', 12), ('999999', 60), ('invalid', 2)])
async def test_rate_limit_retry_after_is_bounded(monkeypatch, refresh_mocks, retry_after, expected):
    sleep, replace = refresh_mocks
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': retry_after})
        return httpx.Response(200, text=feed('alpha') if len(calls) == 2 else feed())
    transport(monkeypatch, handle)
    await catalog.refresh()
    assert sleep.await_args_list[0].args == (expected,)
    replace.assert_awaited_once()


@pytest.mark.anyio
async def test_malformed_feed_does_not_retry_or_replace(monkeypatch, refresh_mocks):
    sleep, replace = refresh_mocks
    transport(monkeypatch, lambda request: httpx.Response(200, text='<broken'))
    with pytest.raises(catalog.CatalogUnavailable, match='stored app list was kept'):
        await catalog.refresh()
    sleep.assert_not_awaited()
    replace.assert_not_awaited()
