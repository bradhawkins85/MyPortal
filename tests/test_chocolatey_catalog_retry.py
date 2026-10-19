"""Targeted inventory lookups, retry behavior, and safe refresh failures."""
from unittest.mock import AsyncMock
from xml.sax.saxutils import escape

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


def atom_feed(entries):
    """NuGet syndication puts the package Id in Atom title, not d:Id."""
    return ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">' +
            ''.join(f'<entry><title type="text">{escape(package)}</title>'
                    '<content type="application/zip" src="https://example.com/package"/>'
                    f'<m:properties><d:Title>{escape(title)}</d:Title>'
                    '<d:Version>2.0</d:Version><d:IsPrerelease>false</d:IsPrerelease>'
                    '</m:properties></entry>' for package, title in entries) + '</feed>')


@pytest.mark.parametrize('make_feed', [feed, atom_feed])
def test_parse_package_id_from_properties_or_atom_title(make_feed):
    count, rows = catalog._parse(make_feed([
        ('GoogleChrome', 'Google Chrome'), ('bad;id', 'Invalid'),
    ]).encode())
    assert count == 2
    assert rows == [{'package_id': 'googlechrome', 'title': 'Google Chrome',
                     'version': '2.0', 'download_count': 0}]


@pytest.mark.anyio
@pytest.mark.parametrize('status', [400, 406])
async def test_atom_ids_and_ampersand_fallback_match_original_inventory(monkeypatch, mocks, status):
    _, store, names = mocks
    names.return_value = ['Google Chrome', 'Veeam Backup & Replication']
    terms = []

    def handle(request):
        term = request.url.params['searchTerm']
        terms.append(term)
        if '&' in term:
            return httpx.Response(status)
        entries = [('GoogleChrome', 'Google Chrome')] if 'Google' in term else [
            ('veeam', 'Veeam Backup & Replication'),
            ('unrelated', 'Veeam Backup Tools'),
        ]
        return httpx.Response(200, text=atom_feed(entries))

    transport(monkeypatch, handle)
    result = await catalog.refresh()
    assert terms == ["'Google Chrome'", "'Veeam Backup & Replication'", "'Veeam Backup Replication'"]
    assert result['matched'] == result['stored'] == 2
    assert result['unmatched'] == result['rejected'] == 0
    assert store.matches == [{'name': names.return_value[0], 'package_id': 'googlechrome'},
                             {'name': names.return_value[1], 'package_id': 'veeam'}]


@pytest.mark.anyio
async def test_simpler_search_does_not_authorize_simpler_name_match(monkeypatch, mocks):
    _, store, names = mocks
    names.return_value = ['Backup & Replication']

    def handle(request):
        if '&' in request.url.params['searchTerm']:
            return httpx.Response(406)
        return httpx.Response(200, text=atom_feed([('different', 'Backup Replication')]))

    transport(monkeypatch, handle)
    result = await catalog.refresh()
    assert result['matched'] == 0 and result['unmatched'] == 1
    assert store.matches == []


class Store:
    """Records saved batches; ``packages`` lists every package saved, in order."""

    def __init__(self):
        self.packages, self.matches, self.cleared, self.checked = [], [], [], []
        self.save_progress = AsyncMock(side_effect=self._save)
        self.prune = AsyncMock(return_value=0)

    async def _save(self, packages, matches, *, cleared, checked, checked_at):
        self.packages += [p['package_id'] for p in packages]
        self.matches += [dict(m) for m in matches]
        self.cleared += list(cleared)
        self.checked += list(checked)
        return len(packages)


@pytest.fixture
def mocks(monkeypatch):
    sleep = AsyncMock()
    store = Store()
    names = AsyncMock(return_value=['Alpha', 'Beta'])
    monkeypatch.setattr(catalog.asyncio, 'sleep', sleep)
    monkeypatch.setattr(catalog.catalog_repo, 'find_exact', AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog.catalog_repo, 'lookup_times', AsyncMock(return_value={}))
    monkeypatch.setattr(catalog.catalog_repo, 'save_progress', store.save_progress)
    monkeypatch.setattr(catalog.catalog_repo, 'prune', store.prune)
    monkeypatch.setattr(catalog.inventory_repo, 'list_catalog_software_names', names)
    return sleep, store, names


def transport(monkeypatch, handler):
    monkeypatch.setattr(catalog, 'monitored_client', lambda *args, **kwargs:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('name,expected', [
    ('7-Zip 24.09 (x64)', '7-Zip'),
    ('Mozilla Firefox (x64 en-US)', 'Mozilla Firefox'),
    ('Google Chrome (64-bit)', 'Google Chrome'),
    ('7-Zip (Install)', '7-Zip'),
    ('App version 1.2.3', 'App'),
    ('App v1.2.3 (ARM64)', 'App'),
    ('Visual Studio 2022', 'Visual Studio 2022'),
    ('Firefox ESR', 'Firefox ESR'),
    ('App (Portable)', 'App (Portable)'),
    ('App Enterprise', 'App Enterprise'),
])
def test_matching_removes_only_known_packaging_decorations(name, expected):
    assert catalog._application_name(name) == expected


@pytest.mark.anyio
async def test_normalized_matching_still_rejects_ambiguity_editions_and_substrings(monkeypatch, mocks):
    sleep, replace, names = mocks
    names.return_value = ['7-Zip 24.09 (x64)', 'App', 'App Enterprise', 'Firefox ESR']
    responses = {
        "'7-Zip'": [('7zip', '7-Zip (Install)'), ('7zip.install', '7-Zip (Install)')],
        "'App'": [('app-tools', 'App Tools')],
        "'App Enterprise'": [('app', 'App')],
        "'Firefox ESR'": [('firefox', 'Firefox')],
    }
    transport(monkeypatch, lambda req: httpx.Response(200, text=feed(responses[req.url.params['searchTerm']])))
    summary = await catalog.refresh()
    assert summary['ambiguous'] == 1 and summary['unmatched'] == 3
    assert replace.packages == [] and replace.matches == []
    assert sorted(replace.cleared) == sorted(names.return_value)


@pytest.mark.anyio
@pytest.mark.parametrize('name,wire_term', [
    ('7zip', "'7zip'"),
    ('Google Chrome', "'Google%20Chrome'"),
    ("Vendor's App", "'Vendor''s%20App'"),
    ('C++ & Tools (x64)', "'C%2B%2B%20%26%20Tools'"),
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
    calls = []
    def handle(request):
        term = request.url.params['searchTerm']
        calls.append(term)
        if 'WinCT-Moisture' in term:
            return httpx.Response(status)
        name = term.strip("'")
        return httpx.Response(200, text=feed([(name.lower(), name)]))
    transport(monkeypatch, handle)
    result = await catalog.refresh()
    # Only one simplified search is attempted, and later names are still looked up.
    assert calls == ["'Alpha'", "'A&D WinCT-Moisture'", "'A D WinCT-Moisture'", "'Beta'"]
    # The rejected name's stored match is neither replaced nor cleared.
    assert replace.packages == ['alpha', 'beta']
    assert 'A&D WinCT-Moisture' not in replace.cleared
    assert 'A&D WinCT-Moisture' in replace.checked
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
    replace.prune.assert_not_awaited()


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
    assert 'stored match was kept' in message
    assert '\n' not in message
    sleep.assert_not_awaited()
    replace.prune.assert_not_awaited()


@pytest.mark.parametrize('content', ['<html>untrusted error page</html>', '<broken', 'x' * 20_000])
def test_error_does_not_echo_non_odata_responses(content):
    response = httpx.Response(406, text=content)
    assert catalog._lookup_error(response, 'App') == (
        "Chocolatey software lookup failed with HTTP 406 for 'App'; the stored match was kept.")


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
    assert replace.packages == ['alpha', 'beta']
    assert result == {'source': catalog.COMMUNITY, 'applications': 2, 'matched': 2,
                      'unmatched': 0, 'ambiguous': 0, 'rejected': 0, 'rejected_names': [],
                      'failed': 0, 'failed_names': [], 'stored': 2, 'removed': 0}
    assert [c.args[0] for c in sleep.await_args_list] == [catalog.PAGE_DELAY, 2]


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [504, 'timeout'])
async def test_exhausted_retries_skip_only_that_application(monkeypatch, mocks, failure):
    sleep, replace, names = mocks
    names.return_value = ['Alpha', 'Beta', 'Gamma']
    terms = []
    def handle(request):
        term = request.url.params['searchTerm']
        terms.append(term)
        if term == "'Beta'":
            if failure == 'timeout':
                raise httpx.ReadTimeout('upstream timeout', request=request)
            return httpx.Response(failure)
        name = term.strip("'")
        return httpx.Response(200, text=feed([(name.lower(), name)]))
    transport(monkeypatch, handle)
    result = await catalog.refresh()
    assert terms == ["'Alpha'"] + ["'Beta'"] * 4 + ["'Gamma'"]
    # Beta keeps its stored match and is not marked checked, so it is retried first next time.
    assert replace.packages == ['alpha', 'gamma'] and 'Beta' not in replace.cleared + replace.checked
    assert result['failed'] == 1 and result['failed_names'] == ['Beta'] and result['matched'] == 2
    replace.prune.assert_awaited_once_with(['Alpha', 'Beta', 'Gamma'])


@pytest.mark.anyio
async def test_feed_outage_stops_early_but_keeps_progress(monkeypatch, mocks):
    sleep, replace, names = mocks
    names.return_value = ['Alpha'] + [f'App {n}' for n in range(10)]
    def handle(request):
        if request.url.params['searchTerm'] == "'Alpha'":
            return httpx.Response(200, text=feed([('alpha', 'Alpha')]))
        return httpx.Response(503)
    transport(monkeypatch, handle)
    with pytest.raises(catalog.CatalogUnavailable, match='Saved 1 of 11 application lookups'):
        await catalog.refresh()
    assert replace.packages == ['alpha'] and replace.checked == ['Alpha']
    replace.prune.assert_not_awaited()


@pytest.mark.anyio
async def test_progress_is_saved_in_batches_and_oldest_lookups_run_first(monkeypatch, mocks):
    from datetime import datetime
    sleep, replace, names = mocks
    names.return_value = [f'App {n:02}' for n in range(30)]
    monkeypatch.setattr(catalog.catalog_repo, 'lookup_times', AsyncMock(return_value={
        'app 00': datetime(2026, 10, 2), 'app 01': datetime(2026, 10, 1)}))
    terms = []
    def handle(request):
        terms.append(request.url.params['searchTerm'].strip("'"))
        return httpx.Response(200, text=feed())
    transport(monkeypatch, handle)
    await catalog.refresh()
    assert terms[:2] == ['App 02', 'App 03'] and terms[-2:] == ['App 01', 'App 00']
    assert replace.save_progress.await_count == 2
    assert len(replace.save_progress.await_args_list[0].kwargs['checked']) == catalog.SAVE_EVERY


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
    replace.prune.assert_not_awaited()


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
    assert replace.packages == ['alpha']


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
    assert replace.packages == ['alpha', 'vendor']


@pytest.mark.anyio
@pytest.mark.parametrize('names_value', [[], ['Missing']])
async def test_empty_or_unmatched_inventory_clears_old_full_catalog(monkeypatch, mocks, names_value):
    sleep, replace, names = mocks
    names.return_value = names_value
    transport(monkeypatch, lambda request: httpx.Response(200, text=feed()))
    result = await catalog.refresh()
    replace.prune.assert_awaited_once_with(names_value)
    assert replace.packages == []
    assert result['matched'] == 0


@pytest.mark.anyio
@pytest.mark.parametrize('content', ['<broken', '<html/>'])
async def test_malformed_response_does_not_replace(monkeypatch, mocks, content):
    sleep, replace, names = mocks
    transport(monkeypatch, lambda request: httpx.Response(200, text=content))
    result = await catalog.refresh()
    # A malformed response only skips that application.
    assert result['failed'] == 2 and replace.packages == []


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
        CREATE TABLE rmm_chocolatey_software_matches (name TEXT PRIMARY KEY, package_id TEXT);
        CREATE TABLE rmm_chocolatey_lookups (name TEXT PRIMARY KEY, checked_at TEXT);
        INSERT INTO rmm_chocolatey_lookups VALUES ('Removed App', '2026-10-01 00:00:00');
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
        return httpx.Response(200, text=atom_feed([('alpha', 'Alpha')]))
    transport(monkeypatch, handle)
    try:
        assert await catalog.inventory_repo.list_catalog_software_names() == ['Alpha']
        result = await catalog.refresh()
        assert calls == ["'Alpha'"]
        assert result['stored'] == result['removed'] == 1
        assert await catalog.query('') == [{'package_id': 'alpha', 'title': 'Alpha', 'version': '2.0',
                                           'display_name': 'Alpha'}]
        assert await catalog.query('uninstalled') == []
        assert [row['name'] for row in connection.execute('SELECT name FROM rmm_chocolatey_lookups')] == ['Alpha']
    finally:
        connection.close()
