"""Voice monitor migration, validation, and tenant repository tests."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.repositories import voice_monitor as repo
from app.schemas.voice_monitor import DialingPolicy, VoiceMonitorConfiguration


def test_migration_preserves_attempt_audit_history_and_has_workload_indexes():
    sql = Path("migrations/334_voice_monitor.sql").read_text()
    assert "CREATE TABLE voice_monitor_endpoints" in sql
    assert "CREATE TABLE voice_monitor_attempts" in sql
    assert "ON DELETE SET NULL" in sql
    assert "ON DELETE CASCADE" not in sql
    for index in ("idx_voice_monitor_due", "idx_voice_monitor_attempt_history",
                  "uq_voice_monitor_provider_call", "idx_voice_monitor_ticket_dedup"):
        assert index in sql
    for state in ("queued", "dialing", "answered", "passed", "failed", "timed_out", "cancelled"):
        assert f"'{state}'" in sql


def test_configuration_normalizes_e164_and_enforces_configurable_policy():
    config = VoiceMonitorConfiguration(
        destination_e164="+61 412 345 678", display_label="Main line", interval_seconds=300
    )
    assert config.destination_e164 == "+61412345678"

    with pytest.raises(ValidationError, match="country is prohibited"):
        VoiceMonitorConfiguration.model_validate(
            {"destination_e164": "+61412345678", "display_label": "Main", "interval_seconds": 300},
            context={"dialing_policy": DialingPolicy(allowed_country_codes={1})},
        )


def test_customer_attempt_lookup_is_tenant_scoped():
    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as fetch:
        fetch.return_value = None
        asyncio.run(repo.get_attempt(42, 99))
    query, params = fetch.call_args.args
    assert "company_id = %s" in query
    assert params == (99, 42)


def test_ticket_link_is_atomic_and_tenant_scoped():
    with patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock) as execute:
        execute.return_value = 1
        assert asyncio.run(repo.link_ticket_once(7, 11, 13))
    query, params = execute.call_args.args
    assert "created_ticket_id IS NULL" in query
    assert "company_id = %s" in query
    assert params == (13, 11, 7)


def test_due_claim_uses_compare_and_swap_before_creating_attempt():
    due = {"id": 3, "company_id": 7, "next_run_at": datetime(2026, 1, 1)}
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock, return_value=[due]), \
         patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock, return_value=1) as update, \
         patch.object(repo.db, "execute_returning_lastrowid", new_callable=AsyncMock, return_value=21) as insert:
        claimed = asyncio.run(repo.claim_due_work(worker_identity="worker-1", now=datetime(2026, 1, 2)))
    assert claimed[0]["attempt_id"] == 21
    assert "next_run_at = %s" in update.call_args.args[0]
    assert insert.call_args.args[1][0:2] == (3, 7)


def test_attempt_state_transition_rejects_skips():
    with pytest.raises(ValueError, match="invalid attempt transition"):
        asyncio.run(repo.transition_attempt(1, 2, "queued", "passed"))
