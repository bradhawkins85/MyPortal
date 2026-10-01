"""Handler behaviour for the admin issue tracker forms."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.datastructures import FormData

from app.features.issue_tracker import handlers
from app.repositories import issues as issues_repo


class _Request:
    def __init__(self, items):
        self._form = FormData(items)

    async def form(self):
        return self._form


def _patch_common(monkeypatch, allowed_ids):
    fake_main = SimpleNamespace(
        _require_issue_tracker_access=lambda request: _async((({"id": 1}), None)),
        _get_current_user_id=lambda user: 1,
    )
    monkeypatch.setattr(handlers, "_main", lambda: fake_main)

    async def _options(user):
        return [{"id": cid, "name": f"C{cid}"} for cid in allowed_ids], set(allowed_ids)

    monkeypatch.setattr(handlers, "_accessible_company_options", _options)
    monkeypatch.setattr(handlers, "flash_redirect", lambda url, message, kind: (url, message, kind))


def _async(value):
    async def _inner():
        return value

    return _inner()


def test_create_issue_links_companies_posted_as_companyIds(monkeypatch):
    from app.services import issues as issues_service

    _patch_common(monkeypatch, [5, 6])
    assigned = []

    async def _available(name, **kwargs):
        return None

    async def _create(**kwargs):
        return {"issue_id": 42}

    async def _assign(**kwargs):
        assigned.append((kwargs["company_id"], kwargs["status"]))

    monkeypatch.setattr(issues_service, "ensure_issue_name_available", _available)
    monkeypatch.setattr(issues_repo, "create_issue", _create)
    monkeypatch.setattr(issues_repo, "assign_issue_to_company", _assign)

    request = _Request([
        ("name", "Outage"),
        ("initialStatus", "investigating"),
        ("companyIds", "5"),
        ("companyIds", "6"),
        ("companyIds", "99"),  # not accessible
    ])
    result = asyncio.run(handlers.admin_create_issue(request))

    assert result == ("/admin/issues", "Issue created.", "success")
    assert assigned == [(5, "investigating"), (6, "investigating")]


def test_update_allows_issue_with_no_links_but_not_one_linked_elsewhere(monkeypatch):
    _patch_common(monkeypatch, [5])
    assigned = []
    records = {
        1: {"issue_id": 1, "name": "Unlinked", "description": None, "assignments": []},
        2: {"issue_id": 2, "name": "Elsewhere", "description": None, "assignments": [{"company_id": 8}]},
    }

    async def _get(issue_id, company_ids=None):
        record = dict(records[issue_id])
        if company_ids is not None:
            record["assignments"] = [a for a in record["assignments"] if a["company_id"] in company_ids]
        return record

    async def _assign(**kwargs):
        assigned.append((kwargs["issue_id"], kwargs["company_id"]))

    monkeypatch.setattr(issues_repo, "get_issue_by_id", _get)
    monkeypatch.setattr(issues_repo, "assign_issue_to_company", _assign)

    form = [("name", "Unlinked"), ("newCompanyIds", "5"), ("newCompanyStatus", "new")]
    result = asyncio.run(handlers.admin_update_issue(1, _Request(form)))
    assert result == ("/admin/issues", "Issue updated.", "success")
    assert assigned == [(1, 5)]

    form = [("name", "Elsewhere"), ("newCompanyIds", "5")]
    result = asyncio.run(handlers.admin_update_issue(2, _Request(form)))
    assert result == ("/admin/issues", "Issue not found.", "error")
    assert assigned == [(1, 5)]


def test_delete_issue_removes_accessible_issue(monkeypatch):
    _patch_common(monkeypatch, [5, 6])
    deleted = []

    async def _get(issue_id):
        return {
            "issue_id": issue_id,
            "name": "Outage",
            "assignments": [{"company_id": 5}, {"company_id": 6}],
        }

    async def _delete(issue_id):
        deleted.append(issue_id)

    monkeypatch.setattr(issues_repo, "get_issue_by_id", _get)
    monkeypatch.setattr(issues_repo, "delete_issue", _delete)

    result = asyncio.run(handlers.admin_delete_issue(42, _Request([])))

    assert result == ("/admin/issues", "Issue deleted.", "success")
    assert deleted == [42]


def test_delete_issue_rejects_hidden_company_assignment(monkeypatch):
    _patch_common(monkeypatch, [5])
    deleted = []

    async def _get(issue_id):
        return {
            "issue_id": issue_id,
            "name": "Outage",
            "assignments": [{"company_id": 5}, {"company_id": 99}],
        }

    async def _delete(issue_id):
        deleted.append(issue_id)

    monkeypatch.setattr(issues_repo, "get_issue_by_id", _get)
    monkeypatch.setattr(issues_repo, "delete_issue", _delete)

    result = asyncio.run(handlers.admin_delete_issue(42, _Request([])))

    assert result == (
        "/admin/issues",
        "Issue could not be deleted because it is linked to a company you cannot manage.",
        "error",
    )
    assert deleted == []
