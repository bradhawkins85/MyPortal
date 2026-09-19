from unittest.mock import AsyncMock

import pytest

from app.repositories import approval_matrix


@pytest.mark.anyio
async def test_assign_to_ticket_rejects_configuration_from_another_company(monkeypatch):
    monkeypatch.setattr(
        approval_matrix,
        "get_configuration",
        AsyncMock(return_value={"id": 3, "company_id": 8, "workflow_type": "change"}),
    )
    monkeypatch.setattr(
        approval_matrix.db,
        "fetch_one",
        AsyncMock(return_value={"company_id": 9}),
    )

    with pytest.raises(ValueError, match="ticket company"):
        await approval_matrix.assign_to_ticket(ticket_id=4, configuration_id=3)


@pytest.mark.anyio
async def test_set_decision_only_accepts_final_statuses():
    with pytest.raises(ValueError, match="approved or denied"):
        await approval_matrix.set_decision(
            ticket_id=4,
            decision_id=2,
            decision_status="pending",
            decided_by_user_id=1,
        )
