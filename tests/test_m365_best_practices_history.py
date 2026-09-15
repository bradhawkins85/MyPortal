from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.repositories import m365_best_practices as repository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_daily_history_migration_is_portable_and_indexed() -> None:
    sql = Path("migrations/357_m365_best_practice_daily_history.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS m365_best_practice_daily_history" in sql
    assert "UNIQUE INDEX IF NOT EXISTS uq_m365_bp_history_company_date" in sql
    assert "FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE" in sql


def test_parse_secure_score_values() -> None:
    assert repository._parse_secure_score("Secure Score is 42.5/80 (53.1% of maximum).") == (
        42.5,
        80.0,
        53.1,
    )
    assert repository._parse_secure_score("Secure Score is unavailable.") == (None, None, None)


@pytest.mark.anyio
async def test_upsert_daily_history_records_all_statuses_and_secure_score(monkeypatch) -> None:
    fetch_one = AsyncMock(
        return_value={
            "pass_count": 12,
            "fail_count": 3,
            "unknown_count": 2,
            "not_applicable_count": 1,
            "secure_score_details": "Secure Score is 60/100 (60% of maximum).",
        }
    )
    execute = AsyncMock()
    monkeypatch.setattr(repository.db, "fetch_one", fetch_one)
    monkeypatch.setattr(repository.db, "execute", execute)
    recorded_at = datetime(2026, 9, 15, 3, 4, 5)

    await repository._upsert_daily_history(
        company_id=7,
        snapshot_date=date(2026, 9, 15),
        recorded_at=recorded_at,
    )

    params = execute.await_args.args[1]
    assert params == (7, date(2026, 9, 15), 12, 3, 2, 1, 60.0, 100.0, 60.0, recorded_at)
    aggregate_sql = fetch_one.await_args.args[0]
    assert "m365_best_practice_company_exclusions" in aggregate_sql
    assert "s.enabled = 1" in aggregate_sql


@pytest.mark.anyio
async def test_list_daily_history_bounds_the_parameterised_limit(monkeypatch) -> None:
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(repository.db, "fetch_all", fetch_all)

    assert await repository.list_daily_history(9, limit=99999) == []

    sql, params = fetch_all.await_args.args
    assert "LIMIT %s" in sql
    assert params == (9, 3650)
