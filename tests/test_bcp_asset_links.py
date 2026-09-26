from unittest.mock import AsyncMock

import pytest

from app.repositories import bcp as bcp_repo


@pytest.mark.anyio
async def test_link_asset_validates_component_and_asset_company(monkeypatch):
    fetch_one = AsyncMock(side_effect=[{"id": 12}, None])
    monkeypatch.setattr(bcp_repo.db, "fetch_one", fetch_one)
    insert = AsyncMock()
    monkeypatch.setattr(bcp_repo.db, "execute_returning_lastrowid", insert)

    with pytest.raises(ValueError, match="not available"):
        await bcp_repo.link_asset_to_component(
            plan_id=4,
            company_id=8,
            component_type="recovery_action",
            component_id=12,
            asset_id=99,
            linked_by_user_id=3,
        )

    assert fetch_one.await_args_list[1].args[1] == (99, 8)
    insert.assert_not_awaited()


@pytest.mark.anyio
async def test_link_asset_uses_canonical_record_without_creating_asset(monkeypatch):
    fetch_one = AsyncMock(side_effect=[
        {"id": 12},
        {"id": 99, "name": "Printer", "type": "Printer", "serial_number": "P-1"},
        None,
    ])
    monkeypatch.setattr(bcp_repo.db, "fetch_one", fetch_one)
    monkeypatch.setattr(bcp_repo.db, "execute_returning_lastrowid", AsyncMock(return_value=7))
    monkeypatch.setattr(
        bcp_repo,
        "list_component_asset_links",
        AsyncMock(return_value=[{"id": 7, "asset_id": 99}]),
    )

    link = await bcp_repo.link_asset_to_component(
        plan_id=4,
        company_id=8,
        component_type="recovery_action",
        component_id=12,
        asset_id=99,
        linked_by_user_id=3,
    )

    assert link == {"id": 7, "asset_id": 99}
    sql = bcp_repo.db.execute_returning_lastrowid.await_args.args[0]
    assert "INSERT INTO bcp_component_asset_links" in sql
    assert "INSERT INTO assets" not in sql


@pytest.mark.anyio
async def test_unlink_preserves_historical_link(monkeypatch):
    historical = {"id": 7, "asset_id": 99, "asset_name_snapshot": "Old printer"}
    monkeypatch.setattr(bcp_repo.db, "fetch_one", AsyncMock(return_value=historical))
    update = AsyncMock(return_value=1)
    monkeypatch.setattr(bcp_repo.db, "execute", update)

    result = await bcp_repo.unlink_asset_from_component(
        link_id=7, plan_id=4, company_id=8, unlinked_by_user_id=3
    )

    assert result == historical
    assert "UPDATE bcp_component_asset_links" in update.await_args.args[0]
    assert "DELETE" not in update.await_args.args[0]
