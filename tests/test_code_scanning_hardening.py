"""Regression tests for the CodeQL code-scanning fixes.

Each test pins the validation that now sits directly in front of a request,
redirect, SQL statement or filesystem path so the guard cannot silently
regress.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.plugin_loader import PluginLoader
from app.repositories import email_blocklist
from app.security import flash
from app.services import call_recordings, m365_mail, sso_admin, syncro, trello
from app.services.client_onboarding_questions import _PHONE


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# SQL: email blocklist ORDER BY comes from a fixed table of clauses
# ---------------------------------------------------------------------------


class _CapturingDb:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch_all(self, query, params=None):
        self.queries.append(query)
        return []

    async def fetch_one(self, query, params=None):
        self.queries.append(query)
        return {"count": 0}


@pytest.mark.anyio
async def test_email_blocklist_list_uses_static_order_by(monkeypatch):
    fake_db = _CapturingDb()
    monkeypatch.setattr(email_blocklist, "db", fake_db)

    await email_blocklist.list_entries(search="spam", sort="email", direction="ASC")

    query = fake_db.queries[-1]
    assert "ORDER BY email ASC, id ASC" in query
    assert "WHERE email LIKE :search" in query


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sort", "direction"),
    [("email; DROP TABLE users", "asc"), ("email", "asc; --")],
)
async def test_email_blocklist_list_rejects_unknown_sort(monkeypatch, sort, direction):
    monkeypatch.setattr(email_blocklist, "db", _CapturingDb())
    with pytest.raises(ValueError):
        await email_blocklist.list_entries(sort=sort, direction=direction)


@pytest.mark.anyio
async def test_email_blocklist_count_without_search(monkeypatch):
    fake_db = _CapturingDb()
    monkeypatch.setattr(email_blocklist, "db", fake_db)

    assert await email_blocklist.count_entries() == 0
    assert fake_db.queries[-1].strip() == "SELECT COUNT(*) AS count FROM email_blocklist"


# ---------------------------------------------------------------------------
# Redirects and flash cookies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/admin/tickets/42", "/admin/tickets/42"),
        ("/admin/service-status?serviceId=7", "/admin/service-status?serviceId=7"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/%2Fevil.example", "/"),
        ("\\\\evil.example", "/"),
        ("", "/"),
    ],
)
def test_local_redirect_only_targets_same_site_paths(target, expected):
    response = flash.local_redirect(target)
    assert response.status_code == 303
    assert response.headers["location"] == expected


def test_set_flash_replaces_unknown_variant_with_info():
    response = flash.local_redirect("/")
    flash.set_flash(response, "Saved.", "<script>")  # type: ignore[arg-type]
    cookie_value = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]

    class _Request:
        cookies = {"_flash": cookie_value}

    assert flash.pop_flash(_Request()) == {"message": "Saved.", "variant": "info"}


# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------


def test_recordings_path_must_stay_inside_trusted_root(tmp_path):
    root = tmp_path / "recordings"
    (root / "2026").mkdir(parents=True)
    sibling = tmp_path / "recordings-other"
    sibling.mkdir()

    assert call_recordings._validate_recordings_path(str(root / "2026"), str(root)) == (
        root / "2026"
    ).resolve()
    assert call_recordings._validate_recordings_path(str(root), str(root)) == root.resolve()
    for outside in (str(sibling), str(root / ".." / "recordings-other"), "/etc"):
        with pytest.raises(ValueError, match="outside the allowed recordings directory"):
            call_recordings._validate_recordings_path(outside, str(root))


def test_recordings_path_requires_a_trusted_root(tmp_path, monkeypatch):
    monkeypatch.delenv("CALL_RECORDINGS_PATH", raising=False)
    with pytest.raises(ValueError, match="No trusted recordings root"):
        call_recordings._validate_recordings_path(str(tmp_path))


def test_plugin_install_rejects_directories_outside_plugin_root(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "plugins-evil"
    outside.mkdir()
    (outside / "__init__.py").write_text("", encoding="utf-8")
    loader = PluginLoader(plugin_dirs=str(root))

    with pytest.raises(ValueError, match="inside the plugin root"):
        asyncio.run(loader.install_from_directory(str(outside)))
    with pytest.raises(ValueError, match="inside the plugin root"):
        asyncio.run(loader.install_from_directory(str(root / ".." / "plugins-evil")))


# ---------------------------------------------------------------------------
# Outbound request identifiers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_syncro_get_ticket_rejects_non_numeric_ids(monkeypatch):
    async def _unexpected_request(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("request should not be sent")

    monkeypatch.setattr(syncro, "_request", _unexpected_request)
    for bad in ("1/../../users", "12?x=1", "", "abc"):
        with pytest.raises(ValueError):
            await syncro.get_ticket(bad)


@pytest.mark.anyio
async def test_syncro_get_ticket_accepts_integer_ids(monkeypatch):
    calls: list[str] = []

    async def _fake_request(method, path, **kwargs):
        calls.append(path)
        return {"ticket": {"id": 5}}

    monkeypatch.setattr(syncro, "_request", _fake_request)
    assert await syncro.get_ticket(5) == {"id": 5}
    assert calls == ["/tickets/5"]


@pytest.mark.parametrize(
    "card_id",
    ["0123456789abcdef01234567\n", "0123456789ABCDEF01234567", "../boards", "", None],
)
def test_trello_id_pattern_requires_exact_lowercase_hex(card_id):
    assert not (isinstance(card_id, str) and trello._TRELLO_ID_RE.fullmatch(card_id))


def test_trello_id_pattern_accepts_real_ids():
    assert trello._TRELLO_ID_RE.fullmatch("0123456789abcdef01234567")


@pytest.mark.parametrize(
    "url",
    [
        "https://graph.microsoft.com/v1.0/users/a%40b.com/messages/abc",
        "https://graph.microsoft.com/v1.0/users/a%40b.com/mailFolders?$filter=displayName%20eq%20%27X%27&$top=1",
    ],
)
def test_graph_url_validation_keeps_encoded_urls(url):
    assert m365_mail._validate_graph_request_url(url, method="GET") == url


def test_graph_url_validation_rejects_raw_whitespace():
    with pytest.raises(ValueError):
        m365_mail._validate_graph_request_url(
            "https://graph.microsoft.com/v1.0/users/a%40b.com/messages/a b", method="GET"
        )


@pytest.mark.anyio
async def test_sso_directory_search_rejects_control_characters_and_long_terms():
    for term in ("ab\x00cd", "x" * 201):
        with pytest.raises(sso_admin.SsoAdminError):
            await sso_admin.search_directory(1, term)


# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["+61 400 000 000", "(02) 9999-1234", "555 x 12", "555 ext. 99", "555EXT9"]
)
def test_phone_pattern_accepts_extensions(value):
    assert _PHONE.fullmatch(value)


@pytest.mark.parametrize("value", ["abc", "555 x", "x12", "555 ext"])
def test_phone_pattern_rejects_invalid_values(value):
    assert not _PHONE.fullmatch(value)


def test_phone_pattern_is_linear_on_long_space_runs():
    import time

    started = time.perf_counter()
    assert not _PHONE.fullmatch(" " + "  " * 5000 + "!")
    assert time.perf_counter() - started < 1.0
