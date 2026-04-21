"""Static audit-coverage lint test.

Walks every state-changing FastAPI route (POST/PUT/PATCH/DELETE) registered on
the app and inspects the handler's source code for a call to one of the
``app.services.audit`` recording helpers (``record``, ``record_create``,
``record_delete`` or ``log_action``).

Routes that don't yet have an audit call are tracked in
``tests/audit_coverage_allowlist.json``. The list represents the *known*
coverage gap captured at the time it was generated. The test fails if:

* a new write endpoint ships without an audit call **and** without being added
  to the allow-list (preventing the coverage gap from growing further), or
* a route in the allow-list is now audited (the allow-list must shrink as
  follow-up PRs land coverage — please remove the entry).

The intent is described in ``docs/logging-and-audit.md``. The allow-list is
expected to shrink to the empty list ``[]`` over time.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from app.main import app
from fastapi.routing import APIRoute

# Match calls to the recording helpers, e.g.:
#   await audit_service.record(...)
#   await audit.log_action(...)
#   await audit.record_create(...)
_AUDIT_CALL_PATTERN = re.compile(
    r"\baudit(?:_service)?\.(?:record|record_create|record_delete|log_action)\b"
)

_ALLOWLIST_PATH = Path(__file__).parent / "audit_coverage_allowlist.json"


def _collect_audit_sources(endpoint) -> str:
    """Return the handler source plus the source of any helper it directly
    calls inside its own module.

    Many of our handlers delegate to a private helper (e.g.
    ``_handle_shop_product_archive``) that performs both the mutation and the
    audit call. Without following that one level of indirection the lint test
    would flag those handlers as un-audited even though the audit call is in
    the helper invoked unconditionally on the success path.
    """

    try:
        source = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return ""

    module = inspect.getmodule(endpoint)
    if module is None:
        return source

    # Find unqualified names called inside the handler that resolve to
    # callables in the same module, and append their source code. We only
    # follow one level deep to keep the test fast and to avoid pathological
    # call graphs.
    name_pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    self_name = endpoint.__name__
    seen: set[str] = {self_name}
    extra_sources: list[str] = []
    for match in name_pattern.finditer(source):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        target = getattr(module, name, None)
        if target is None or not callable(target):
            continue
        if getattr(target, "__module__", None) != module.__name__:
            continue
        try:
            extra_sources.append(inspect.getsource(target))
        except (OSError, TypeError):
            continue

    if extra_sources:
        return source + "\n" + "\n".join(extra_sources)
    return source


def _enumerate_write_endpoints() -> tuple[set[str], dict[str, str]]:
    """Return the set of unaudited "<METHOD> <path>" entries, plus a debug map.

    The debug map is "<METHOD> <path>" -> "<module>.<function>" so failure
    messages tell humans exactly where to look.
    """

    unaudited: set[str] = set()
    handlers: dict[str, str] = {}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods - {"GET", "HEAD", "OPTIONS"}
        if not methods:
            continue

        endpoint = route.endpoint
        source = _collect_audit_sources(endpoint)
        is_audited = bool(_AUDIT_CALL_PATTERN.search(source))

        for method in methods:
            key = f"{method} {route.path}"
            handlers[key] = f"{endpoint.__module__}.{endpoint.__name__}"
            if not is_audited:
                unaudited.add(key)

    return unaudited, handlers


def _load_allowlist() -> set[str]:
    if not _ALLOWLIST_PATH.exists():
        return set()
    with _ALLOWLIST_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise AssertionError(
            f"{_ALLOWLIST_PATH} must contain a JSON list of "
            f'"<METHOD> <path>" strings'
        )
    return {str(item) for item in data}


def test_state_changing_routes_have_audit_logging():
    """Every write endpoint must call audit.record / log_action.

    Exceptions are tracked in ``tests/audit_coverage_allowlist.json`` and
    should shrink over time.
    """

    unaudited, handlers = _enumerate_write_endpoints()
    allowlist = _load_allowlist()

    new_gaps = sorted(unaudited - allowlist)
    fixed_routes = sorted(allowlist - unaudited)

    messages: list[str] = []
    if new_gaps:
        details = "\n".join(f"  - {key}  ({handlers.get(key, '?')})" for key in new_gaps)
        messages.append(
            "The following state-changing routes are missing an audit log call.\n"
            "Add `await audit_service.record(...)` (or `record_create` / "
            "`record_delete`) in the handler, or — if the route truly should "
            "not be audited — add it to "
            f"{_ALLOWLIST_PATH.name} with a justification in the PR.\n"
            f"{details}"
        )
    if fixed_routes:
        details = "\n".join(f"  - {key}" for key in fixed_routes)
        messages.append(
            "The following routes are in the audit-coverage allow-list but now "
            "have audit logging — please remove them from "
            f"{_ALLOWLIST_PATH.name} so the gap list keeps shrinking:\n"
            f"{details}"
        )

    if messages:
        pytest.fail("\n\n".join(messages))


def test_allowlist_is_well_formed():
    """The allow-list file must be valid JSON, sorted, and contain no dupes."""

    if not _ALLOWLIST_PATH.exists():
        pytest.skip(f"{_ALLOWLIST_PATH.name} not present")
    with _ALLOWLIST_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, list), "allow-list must be a JSON list"
    assert all(isinstance(item, str) for item in data), "entries must be strings"
    assert len(data) == len(set(data)), "allow-list contains duplicates"
    assert data == sorted(data), "allow-list must be sorted alphabetically"
    for item in data:
        # Format: "<METHOD> <path>"
        assert " " in item, f"malformed entry (missing space): {item!r}"
        method = item.split(" ", 1)[0]
        assert method in {"POST", "PUT", "PATCH", "DELETE"}, (
            f"unexpected method in allow-list entry: {item!r}"
        )
