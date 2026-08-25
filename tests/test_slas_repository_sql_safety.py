import pytest

from app.repositories import slas as slas_repo


class _MockDB:
    def __init__(self):
        self.fetch_all_calls: list[tuple[str, tuple]] = []

    async def fetch_all(self, sql, params):
        self.fetch_all_calls.append((sql, params))
        return []


@pytest.mark.anyio
async def test_list_ticket_sla_source_uses_bound_int_params(monkeypatch):
    dummy_db = _MockDB()
    monkeypatch.setattr(slas_repo, "db", dummy_db)

    await slas_repo.list_ticket_sla_source(["1", 2, 3])

    assert len(dummy_db.fetch_all_calls) == 1
    sql, params = dummy_db.fetch_all_calls[0]
    assert "__TICKET_IDS__" not in sql
    assert "WHERE t.id IN (%s,%s,%s)" in sql
    assert params == (1, 2, 3)


@pytest.mark.anyio
async def test_list_pause_periods_uses_bound_int_params(monkeypatch):
    dummy_db = _MockDB()
    monkeypatch.setattr(slas_repo, "db", dummy_db)

    await slas_repo.list_pause_periods(["4", 5])

    assert len(dummy_db.fetch_all_calls) == 1
    sql, params = dummy_db.fetch_all_calls[0]
    assert "__TICKET_IDS__" not in sql
    assert sql.count("IN (%s,%s)") == 2
    assert params == (4, 5, 4, 5)
