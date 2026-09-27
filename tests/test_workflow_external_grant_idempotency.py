from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.repositories import credential_grants


@pytest.mark.anyio
async def test_workflow_external_retry_recovers_same_encrypted_token(monkeypatch):
    token = "one-bearer-token"
    ciphertext = credential_grants.encrypt_secret(token)
    insert = AsyncMock(side_effect=[41, RuntimeError("duplicate key")])
    fetch = AsyncMock(
        side_effect=[
            {"current_version": 3},
            {
                "id": 41,
                "revoked_at": None,
                "consumed_at": None,
                "workflow_token_ciphertext": ciphertext,
            },
            {"current_version": 3},
            {
                "id": 41,
                "revoked_at": None,
                "consumed_at": None,
                "workflow_token_ciphertext": ciphertext,
            },
        ]
    )
    monkeypatch.setattr(credential_grants.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(credential_grants.db, "fetch_one", fetch)

    kwargs = dict(
        credential_id=7,
        company_id=2,
        staff_id=4,
        grantor_user_id=5,
        recipient_email="outside@example.test",
        reason="handover",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        token=token,
        verification_code="separate",
        workflow_execution_id=8,
        workflow_step_identity="share:vendor",
    )
    first = await credential_grants.create_workflow_external(**kwargs)
    retry = await credential_grants.create_workflow_external(**kwargs)

    assert first is not None and retry is not None
    assert first[0]["id"] == retry[0]["id"] == 41
    assert first[1] == retry[1] == token
    assert "one-bearer-token" not in repr(insert.await_args_list)


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_field", ["revoked_at", "consumed_at"])
async def test_workflow_external_terminal_grant_never_rotates_link(
    monkeypatch, terminal_field
):
    row = {
        "id": 41,
        "revoked_at": None,
        "consumed_at": None,
        "workflow_token_ciphertext": credential_grants.encrypt_secret("old"),
    }
    row[terminal_field] = datetime.now(timezone.utc)
    monkeypatch.setattr(
        credential_grants.db,
        "fetch_one",
        AsyncMock(side_effect=[{"current_version": 3}, row]),
    )
    insert = AsyncMock(side_effect=RuntimeError("duplicate key"))
    monkeypatch.setattr(credential_grants.db, "execute_returning_lastrowid", insert)

    result = await credential_grants.create_workflow_external(
        credential_id=7,
        company_id=2,
        staff_id=4,
        grantor_user_id=5,
        recipient_email="outside@example.test",
        reason="handover",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        token="new",
        verification_code="separate",
        workflow_execution_id=8,
        workflow_step_identity="share:vendor",
    )

    assert result is None
    assert insert.await_count == 1
