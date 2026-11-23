import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The weak-secret refusal only relaxes when ENVIRONMENT is explicitly
# development/test, so the suite declares itself as a test environment.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "user")
os.environ.setdefault("DB_PASSWORD", "password")
os.environ.setdefault("DB_NAME", "testdb")
# Anonymisation appends to the erasure ledger; keep it out of the checkout.
os.environ.setdefault(
    "PRIVACY_ERASURE_LEDGER_PATH",
    os.path.join(tempfile.mkdtemp(prefix="myportal-ledger-"), "erasure-ledger.jsonl"),
)

# python-magic needs the native libmagic library. Load the real module once so
# no test file can swap in its own stub for the rest of the run; only fall back
# to a stub where libmagic is genuinely missing.
try:
    import magic  # noqa: F401
except ImportError:  # pragma: no cover - depends on the host's libmagic
    import types

    _magic_stub = types.ModuleType("magic")
    _magic_stub.Magic = lambda *args, **kwargs: None
    _magic_stub.from_buffer = lambda *args, **kwargs: "application/octet-stream"
    sys.modules["magic"] = _magic_stub


async def drain_provision_background_tasks() -> None:
    """Await any pending ``provision_roles_*`` background tasks created by
    :func:`~app.services.m365.provision_app_registration`.

    Call this helper inside the active ``patch.object`` context after
    ``provision_app_registration`` returns so that the background role-grant
    task completes while mocks are still in effect and assertions on Graph
    API call counts are accurate.
    """
    tasks = [
        t for t in asyncio.all_tasks()
        if (t.get_name() or "").startswith("provision_roles_")
        and not t.done()
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture(autouse=True)
def _isolated_erasure_ledger(tmp_path, monkeypatch):
    """Give each test its own erasure ledger once the module is loaded."""
    module = sys.modules.get("app.services.erasure_ledger")
    if module is None:
        yield
        return
    monkeypatch.setattr(module, "ledger_path", lambda: tmp_path / "erasure-ledger.jsonl")
    module._invalidate_index()
    yield
    module._invalidate_index()


def _app_modules() -> dict:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }


@pytest.fixture(autouse=True)
def _restore_app_modules():
    """Put back any ``app`` module a test removed or replaced.

    App shutdown unloads feature packs and drops their modules, and some tests
    re-import packages fresh. Later tests hold the original module objects, so
    a new copy in ``sys.modules`` makes their patches land on the wrong object.
    Restored feature packs are simply mounted again on the next app startup;
    the registry keeps the app's lifespan and wraps copies of each route, so
    re-mounting the same router is safe.
    """
    before = _app_modules()
    yield
    for name in sorted(before, key=len):
        original = before[name]
        if sys.modules.get(name) is not original:
            sys.modules[name] = original
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name) if parent_name else None
        if parent is not None and parent.__dict__.get(child) is not original:
            setattr(parent, child, original)


@pytest.fixture(autouse=True)
def _restore_cached_settings():
    """Hand the original ``Settings`` back to ``get_settings()`` after each test.

    Tests that call ``get_settings.cache_clear()`` under a patched environment
    otherwise leave the next test with settings built from that environment,
    and with an object that differs from the one modules captured at import.
    """
    from app.core import config

    original = config.get_settings()
    yield
    if not hasattr(config.get_settings, "cache_clear"):
        return  # monkeypatched by the test; its own teardown restores it
    if config.get_settings() is original:
        return
    config.get_settings.cache_clear()
    settings_class = config.Settings
    config.Settings = lambda: original
    try:
        config.get_settings()
    finally:
        config.Settings = settings_class


TEST_CSRF_TOKEN = "test-csrf-token"


def csrf_session(user_id: int = 1, csrf_token: str = TEST_CSRF_TOKEN):
    """A session whose CSRF token tests can send in the ``X-CSRF-Token`` header."""
    from datetime import datetime, timedelta, timezone

    from app.security.session import SessionData

    now = datetime.now(timezone.utc)
    return SessionData(
        id=user_id, user_id=user_id, session_token="session-" + str(user_id),
        csrf_token=csrf_token, created_at=now, expires_at=now + timedelta(hours=1),
        last_seen_at=now, ip_address=None, user_agent=None,
    )



def built_middleware(app, middleware_type):
    """Return *app*'s live instance of *middleware_type*.

    Middleware keeps the ``Settings`` object that existed when the stack was
    built, which is not ``app.main.settings`` once another test has called
    ``get_settings.cache_clear()``. Patch the instance's own state instead.
    """
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while node is not None:
        if isinstance(node, middleware_type):
            return node
        node = getattr(node, "app", None)
    raise LookupError(middleware_type.__name__ + " is not installed")


def reset_rate_limiters() -> None:
    """Forget in-memory rate-limit history so earlier tests cannot cause a 429."""
    main = sys.modules.get("app.main")
    if main is None:
        return
    main.general_rate_limiter._events.clear()
    for limiter in main.endpoint_limiter._limiters.values():
        limiter._events.clear()


# ---------------------------------------------------------------------------
# Known-failure baseline
# ---------------------------------------------------------------------------
#
# ``tests/known_failures.txt`` lists tests that were already failing when the
# suite was first run in CI. Each one still runs, marked as a strict xfail:
#
# * a listed test that still fails is reported as ``xfailed``;
# * a listed test that now passes fails the run (``XPASS(strict)``) until its
#   line is deleted, so the list only ever shrinks;
# * any test not on the list fails the run as normal.
#
# Fix a listed test, delete its line, and keep the list sorted.

KNOWN_FAILURES_PATH = Path(__file__).with_name("known_failures.txt")


def _load_known_failures() -> frozenset[str]:
    try:
        lines = KNOWN_FAILURES_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return frozenset()
    return frozenset(
        line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


def pytest_collection_modifyitems(config, items):
    known = _load_known_failures()
    if not known:
        return
    marker = pytest.mark.xfail(
        strict=True,
        reason="Known failure listed in tests/known_failures.txt; fix it and delete the line.",
    )
    for item in items:
        if item.nodeid in known:
            item.add_marker(marker)


def pytest_sessionfinish(session, exitstatus):
    """Stop sqlite worker threads a test forgot to close.

    aiosqlite runs each connection on a non-daemon thread, so one leaked
    connection keeps the interpreter alive after the last test finishes.
    """
    import gc

    try:
        import aiosqlite
    except ImportError:  # pragma: no cover - aiosqlite is a core dependency
        return
    for obj in gc.get_objects():
        if isinstance(obj, aiosqlite.Connection) and obj._running:
            obj.stop()
