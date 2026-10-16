"""Targeted inventory lookups, retry behavior, and safe refresh failures."""
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import chocolatey_catalog as catalog


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def feed(entries=()):
    return ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">' +
            ''.join(f'<entry><m:properties><d:Id>{package}</d:Id><d:Title>{title}</d:Title>'
                    '<d:Version>2.0</d:Version><d:IsPrerelease>false</d:IsPrerelease>'
                    '</m:properties></entry>' for package, title in entries) + '</feed>')


@pytest.fixture
def mocks(monkeypatch):
    sleep = AsyncMock()
    replace = AsyncMock(return_value={'stored': 2, 'removed': 0})
    names = AsyncMock(return_value=['Alpha', 'Beta'])
    monkeypatch.setattr(catalog.asyncio, 'sleep', sleep)
    monkeypatch.setattr(catalog.catalog_repo, 'find_exact', AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog.catalog_repo, 'replace_all', replace)
    monkeypatch.setattr(catalog.inventory_repo, 'list_catalog_software_names', names)
    return sleep, replace, names


def transport(monkeypatch, handler):
    monkeypatch.setattr(catalog, 'monitored_client', lambda *args, **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.anyio
@pytest.mark.parametrize('name,wire_term', [
    ('7zip', "'7zip'"),
    ('Google Chrome', "'Google%20Chrome'"),
    ("Vendor's App", "'Vendor''s%20App'"),
    ('C++ & Tools (x64)', "'C%2B%2B%20%26%20Tools%20%28x64%29'"),
    ('Tools?x=1#tag', "'Tools%3Fx%3D1%23tag'"),
    ('Café', "'Caf%C3%A9'"),
])
async def test_search_matches_chocolatey_nuget_wire_format(monkeypatch, mocks, name, wire_term):
    sleep, replace, names = mocks
    names.return_value = [name]
    # Fixture from Chocolatey's NuGet.Client V2FeedQueryBuilder and UriUtility,
    # asserted on the transmitted bytes rather than HTTPX's decoded params.
    expected = ("$filter=IsLatestVersion&$orderby=Id&searchTerm=" + wire_term +
                "&targetFramework=''&includePrerelease=false&$skip=0&$top=30&semVerLevel=2.0.0").encode()
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.query == expected
        assert request.url.path == '/api/v2/Search()'
        return httpx.Response(200, text=feed())
    transport(monkeypatch, handle)
    await catalog.refresh()
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('status', [400, 406])
async def test_rejected_name_is_skipped_and_keeps_its_stored_match(monkeypatch, mocks, status):
    sleep, replace, names = mocks
    names.return_value = ['Alpha', 'A&D WinCT-Moisture', 'Beta']
    previous = {'package_id': 'ad-winct', 'title': 'A&D WinCT-Moisture', 'version': '1.0', 'download_count': 5}
    find_exact = AsyncMock(return_value=[previous])
    monkeypatch.setattr(catalog.catalog_repo, 'find_exact', find_exact)
    calls = []
    def handle(request):
        term = request.url.params['searchTerm']
        calls.append(term)
        if '&' in term:
            return httpx.Response(status)
        name = term.strip("'")
        return httpx.Response(200, text=feed([(name.lower(), name)]))
    transport(monkeypatch, handle)
    result = await catalog.refresh()
    # Rejections are not retried, and later names are still looked up.
    assert calls == ["'Alpha'", "'A&D WinCT-Moisture'", "'Beta'"]
    find_exact.assert_awaited_once_with('A&D WinCT-Moisture', catalog.RESULT_LIMIT)
    assert [p['package_id'] for p in replace.await_args.args[0]] == ['alpha', 'ad-winct', 'beta']
    assert result['matched'] == 2 and result['unmatched'] == result['ambiguous'] == 0
    assert result['rejected'] == 1 and result['rejected_names'] == ['A&D WinCT-Moisture']


@pytest.mark.anyio
@pytest.mark.parametrize('status', [400, 406])
async def test_all_rejected_does_not_replace(monkeypatch, mocks, status):
    sleep, replace, names = mocks
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status)
    transport(monkeypatch, handle)
    with pytest.raises(catalog.CatalogUnavailable, match=f"HTTP {status} for 'Alpha'"):
        await catalog.refresh()
    assert len(calls) == 2
    replace.assert_not_awaited()


@pytest.mark.anyio
async def test_406_reports_application_and_odata_error_without_replacing_list(monkeypatch, mocks):
    sleep, replace, names = mocks
    names.return_value = ['Google Chrome']
    xml = '''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
        <error xmlns="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
        <code></code><message xml:lang="en-US">Invalid or unsupported parameters used for package lookups,
        unable to continue. Invalid Reason: InvalidQueryParameters.</message></error>'''
    transport(monkeypatch, lambda request: httpx.Response(406, text=xml))
    with pytest.raises(catalog.CatalogUnavailable) as exc:
        await catalog.refresh()
    message = str(exc.value)
    assert "HTTP 406 for 'Google Chrome'" in message
    assert 'Invalid Reason: InvalidQueryParameters.' in message
    assert 'stored app list was kept' in message
    assert '\n' not in message
    sleep.assert_not_awaited()
    replace.assert_not_awaited()


@pytest.mark.parametrize('content', ['<html>untrusted error page</html>', '<broken', 'x' * 20_000])
def test_error_does_not_echo_non_odata_responses(content):
    response = httpx.Response(406, text=content)
    assert catalog._lookup_error(response, 'App') == (
        "Chocolatey software lookup failed with HTTP 406 for 'App'; the stored app list was kept.")


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [429, 500, 502, 503, 504, 'timeout', 'connection'])
async def test_refresh_retries_same_application(monkeypatch, mocks, failure):
    sleep, replace, names = mocks
    terms = []

    def handle(request):
        term = request.url.params['searchTerm']
        terms.append(term)
        assert request.url.path.endswith('/Search()')
        assert request.url.params['$skip'] == '0'
        assert request.url.params['$filter'] == 'IsLatestVersion'
        assert request.url.params['includePrerelease'] == 'false'
        if len(terms) == 2:
            if failure == 'timeout':
                raise httpx.ReadTimeout('upstream timeout', request=request)
            if failure == 'connection':
                raise httpx.ConnectError('connection failed', request=request)
            return httpx.Response(failure)
        name = term.strip("'")
        return httpx.Response(200, text=feed([(name.lower(), name)]))

    transport(monkeypatch, handle)
    result = await catalog.refresh()
    assert terms == ["'Alpha'", "'Beta'", "'Beta'"]
    assert [p['package_id'] for p in replace.await_args.args[0]] == ['alpha', 'beta']
    assert result == {'source': catalog.COMMUNITY, 'applications': 2, 'matched': 2,
                      'unmatched': 0, 'ambiguous': 0, 'rejected': 0, 'rejected_names': [],
                      'stored': 2, 'removed': 0}
    assert [c.args[0] for c in sleep.await_args_list] == [catalog.PAGE_DELAY, 2]


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [504, 'timeout'])
async def test_exhausted_retries_do_not_save_partial_matches(monkeypatch, mocks, failure):
    sleep, replace, names = mocks
    terms = []
    def handle(request):
        term = request.url.params['searchTerm']
        terms.append(term)
        if term == "'Alpha'":
            return httpx.Response(200, text=feed([('alpha', 'Alpha')]))
        if failure == 'timeout':
            raise httpx.ReadTimeout('upstream timeout', request=request)
        return httpx.Response(failure)
    transport(monkeypatch, handle)
    with pytest.raises(catalog.CatalogUnavailable, match='stored app list was kept'):
        await catalog.refresh()
    assert terms == ["'Alpha'"] + ["'Beta'"] * 4
    assert [c.args[0] for c in sleep.await_args_list] == [catalog.PAGE_DELAY, 2, 4, 8]
    replace.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('status', [401, 403, 404])
async def test_permanent_errors_are_not_retried(monkeypatch, mocks, status):
    sleep, replace, names = mocks
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
async def test_retry_after_is_bounded(monkeypatch, mocks, retry_after, expected):
    sleep, replace, names = mocks
    names.return_value = ['Alpha']
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': retry_after})
        return httpx.Response(200, text=feed([('alpha', 'Alpha')]))
    transport(monkeypatch, handle)
    await catalog.refresh()
    assert sleep.await_args_list[0].args == (expected,)
    replace.assert_awaited_once()


@pytest.mark.anyio
async def test_only_inventory_names_are_queried_and_exact_matches_saved(monkeypatch, mocks):
    sleep, replace, names = mocks
    names.return_value = [' Alpha ', 'alpha', 'Beta', "Vendor's App", 'Missing', 'Ambiguous', '']
    responses = {
        "'alpha'": [('alpha', 'Alpha'), ('unrelated', 'Alpha Tools')],
        "'Beta'": [('alpha', 'Beta')],
        "'Vendor''s App'": [('vendor', "Vendor's App")],
        "'Missing'": [('other', 'Missing Tools')],
        "'Ambiguous'": [('one', 'Ambiguous'), ('two', 'Ambiguous')],
    }
    calls = []
    def handle(request):
        term = request.url.params['searchTerm']
        calls.append(term)
        assert term in responses  # A blank or uninstalled search is never allowed.
        return httpx.Response(200, text=feed(responses[term]))
    transport(monkeypatch, handle)
    result = await catalog.refresh()
    assert calls == list(responses)
    assert result['applications'] == 5 and result['matched'] == 3
    assert result['unmatched'] == result['ambiguous'] == 1
    assert [p['package_id'] for p in replace.await_args.args[0]] == ['alpha', 'vendor']


@pytest.mark.anyio
@pytest.mark.parametrize('names_value', [[], ['Missing']])
async def test_empty_or_unmatched_inventory_clears_old_full_catalog(monkeypatch, mocks, names_value):
    sleep, replace, names = mocks
    names.return_value = names_value
    transport(monkeypatch, lambda request: httpx.Response(200, text=feed()))
    result = await catalog.refresh()
    replace.assert_awaited_once()
    assert replace.await_args.args[0] == []
    assert result['matched'] == 0


@pytest.mark.anyio
@pytest.mark.parametrize('content', ['<broken', '<html/>'])
async def test_malformed_response_does_not_replace(monkeypatch, mocks, content):
    sleep, replace, names = mocks
    transport(monkeypatch, lambda request: httpx.Response(200, text=content))
    with pytest.raises(catalog.CatalogUnavailable, match='stored app list was kept'):
        await catalog.refresh()
    sleep.assert_not_awaited()
    replace.assert_not_awaited()


@pytest.mark.anyio
async def test_ui_search_never_calls_public_catalog_even_when_empty(monkeypatch):
    monkeypatch.setattr(catalog.catalog_repo, 'search', AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog.catalog_repo, 'find_exact', AsyncMock(return_value=[]))
    def no_network(*args, **kwargs):
        raise AssertionError('Only the scheduled inventory refresh may contact Chocolatey')
    monkeypatch.setattr(catalog, 'monitored_client', no_network)
    assert await catalog.query('') == []
    assert await catalog.query('not installed') == []
    assert await catalog.match('not installed') is None


@pytest.mark.anyio
async def test_inventory_selection_and_catalog_replacement_sql(monkeypatch):
    import sqlite3

    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE rmm_agents (id INTEGER PRIMARY KEY, status TEXT, os TEXT);
        CREATE TABLE rmm_device_software (agent_id INTEGER, name TEXT, version TEXT);
        CREATE TABLE rmm_chocolatey_packages (package_id TEXT PRIMARY KEY, title TEXT,
            version TEXT, download_count INTEGER, updated_at TEXT);
        INSERT INTO rmm_agents VALUES (1, 'active', 'windows'), (2, 'revoked', 'windows'),
            (3, 'active', 'linux'), (4, 'active', 'Windows');
        INSERT INTO rmm_device_software VALUES (1, 'Alpha', '1'), (1, 'Alpha', '2'),
            (2, 'Revoked App', '1'), (3, 'Linux App', '1'), (4, 'Alpha', '3'), (1, ' ', '1');
        INSERT INTO rmm_chocolatey_packages VALUES ('uninstalled', 'Uninstalled', '1', 0, NULL);
    ''')

    class Database:
        def is_sqlite(self):
            return True

        async def fetch_all(self, sql, params=()):
            return [dict(row) for row in connection.execute(sql.replace('%s', '?'), params)]

        async def execute_many(self, sql, rows):
            # Match the production DB parameter binding while avoiding worker threads.
            rows = [tuple(value.isoformat() if hasattr(value, 'isoformat') else value for value in row)
                    for row in rows]
            connection.executemany(sql.replace('%s', '?'), rows)

    database = Database()
    monkeypatch.setattr(catalog.inventory_repo, 'db', database)
    monkeypatch.setattr(catalog.catalog_repo, 'db', database)
    calls = []
    def handle(request):
        calls.append(request.url.params['searchTerm'])
        return httpx.Response(200, text=feed([('alpha', 'Alpha')]))
    transport(monkeypatch, handle)
    try:
        assert await catalog.inventory_repo.list_catalog_software_names() == ['Alpha']
        result = await catalog.refresh()
        assert calls == ["'Alpha'"]
        assert result['stored'] == result['removed'] == 1
        assert await catalog.query('') == [{'package_id': 'alpha', 'title': 'Alpha', 'version': '2.0'}]
        assert await catalog.query('uninstalled') == []
    finally:
        connection.close()
