import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.dmarc import routes


def test_dmarc_context_allows_read_role_and_blocks_management(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    main = SimpleNamespace(
        _require_authenticated_user=AsyncMock(return_value=(user, None))
    )
    monkeypatch.setattr(routes, "_main", lambda: main)
    monkeypatch.setattr(
        routes.memberships,
        "get_user_company",
        AsyncMock(return_value={"menu_permissions": {"menu.dmarc": "read"}}),
    )

    assert asyncio.run(routes._context(SimpleNamespace())) == (user, 42)
    with pytest.raises(HTTPException, match="DMARC permission required") as error:
        asyncio.run(routes._context(SimpleNamespace(), "dmarc.manage"))

    assert error.value.status_code == 403


def test_dmarc_context_allows_write_role_to_manage(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    main = SimpleNamespace(
        _require_authenticated_user=AsyncMock(return_value=(user, None))
    )
    monkeypatch.setattr(routes, "_main", lambda: main)
    monkeypatch.setattr(
        routes.memberships,
        "get_user_company",
        AsyncMock(return_value={"menu_permissions": {"menu.dmarc": "write"}}),
    )

    assert asyncio.run(routes._context(SimpleNamespace(), "dmarc.manage")) == (user, 42)


def test_dmarc_page_does_not_load_reporting_addresses(monkeypatch):
    user = {"id": 7, "company_id": 42, "is_super_admin": False}
    render = AsyncMock(return_value="response")
    monkeypatch.setattr(
        routes, "_context_with_access", AsyncMock(return_value=(user, 42, True))
    )
    monkeypatch.setattr(routes.repo, "policy_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        routes.repo,
        "overview",
        AsyncMock(return_value={"total_messages": 0, "forensic_reports": 0}),
    )
    monkeypatch.setattr(
        routes.repo, "organization_summary", AsyncMock(return_value=[])
    )
    reporting_addresses = AsyncMock(return_value=["dmarc@example.com"])
    monkeypatch.setattr(
        routes.dmarc, "company_reporting_addresses", reporting_addresses
    )
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(_render_template=render))

    assert asyncio.run(routes.page(SimpleNamespace())) == "response"
    reporting_addresses.assert_not_awaited()
    extra = render.await_args.kwargs["extra"]
    assert "can_manage_dmarc" not in extra
    assert "reporting_addresses" not in extra


def test_dmarc_template_hides_removed_and_empty_sections():
    template = Path("app/templates/dmarc/index.html").read_text(encoding="utf-8")

    assert "Reporting addresses" not in template
    assert "Disposition statistics" in template
    assert "Forensic detail availability" in template
    assert (
        "{% if (metrics.total_messages or 0) > 0 or "
        "(metrics.forensic_reports or 0) > 0 %}"
    ) in template


def test_dmarc_range_rejects_more_than_one_year():
    from datetime import datetime, timezone

    with pytest.raises(HTTPException, match="no more than 366 days"):
        routes._range(
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
