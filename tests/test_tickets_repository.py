import pytest

from app.repositories import tickets


from datetime import datetime, timezone


class _DummyTicketDB:
    def __init__(self, fetched_row):
        self.insert_sql: str | None = None
        self.insert_params: tuple | None = None
        self.fetch_sql: str | None = None
        self.fetch_params: tuple | None = None
        self._fetched_row = fetched_row

    async def execute_returning_lastrowid(self, sql, params):
        self.insert_sql = sql.strip()
        self.insert_params = params
        return 42

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._fetched_row


class _UpdateTicketDB:
    def __init__(self, row):
        self.execute_sql = None
        self.execute_params = None
        self.fetch_sql = None
        self.fetch_params = None
        self._row = row

    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._row


class _FetchReplyDB:
    def __init__(self, row):
        self.fetch_sql = None
        self.fetch_params = None
        self._row = row

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._row


class _BulkDeleteDB:
    def __init__(self, count):
        self.execute_sql = None
        self.execute_params = None
        self.fetch_sql = None
        self.fetch_params = None
        self._count = count

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return {"total": self._count}

    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params


class _ListTicketsDB:
    def __init__(self):
        self.fetch_sql = None
        self.fetch_params = None

    async def fetch_all(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return []


class _ListTicketAssetsDB:
    def __init__(self, rows):
        self.fetch_sql = None
        self.fetch_params = None
        self._rows = rows

    async def fetch_all(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._rows


class _ReplaceTicketAssetsDB:
    def __init__(self, existing_rows, final_rows):
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self._existing_rows = existing_rows
        self._final_rows = final_rows

    async def fetch_all(self, sql, params):
        sql_clean = sql.strip()
        self.fetch_calls.append((sql_clean, params))
        if "INNER JOIN assets" in sql_clean:
            return self._final_rows
        return self._existing_rows

    async def execute(self, sql, params):
        self.execute_calls.append((sql.strip(), params))


class _CountTicketsDB:
    def __init__(self, count):
        self.fetch_sql = None
        self.fetch_params = None
        self._count = count

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return {"count": self._count}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_returns_inserted_record(monkeypatch):
    fetched = {
        "id": 42,
        "company_id": None,
        "requester_id": 7,
        "assigned_user_id": None,
        "subject": "Test Ticket",
        "description": "Body",
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "external_reference": None,
        "ai_summary": None,
        "ai_summary_status": None,
        "ai_summary_model": None,
        "ai_resolution_state": None,
        "ai_summary_updated_at": None,
        "ai_tags": "[\"printer\", \"hardware\"]",
        "ai_tags_status": "succeeded",
        "ai_tags_model": "llama3",
        "ai_tags_updated_at": None,
        "created_at": None,
        "updated_at": None,
        "closed_at": None,
    }
    dummy_db = _DummyTicketDB(fetched)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.create_ticket(
        subject="Test Ticket",
        description="Body",
        requester_id=7,
        company_id=None,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug=None,
        external_reference=None,
    )

    assert record["id"] == 42
    assert record["ai_tags"] == ["printer", "hardware"]
    assert dummy_db.fetch_sql == "SELECT * FROM tickets WHERE id = %s"
    assert dummy_db.fetch_params == (42,)


@pytest.mark.anyio
async def test_create_ticket_falls_back_when_fetch_missing(monkeypatch):
    dummy_db = _DummyTicketDB(fetched_row=None)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.create_ticket(
        subject="Fallback",
        description=None,
        requester_id=None,
        company_id=5,
        assigned_user_id=11,
        priority="urgent",
        status="new",
        category="support",
        module_slug="tickets",
        external_reference="ABC",
    )

    assert record["id"] == 42
    assert record["company_id"] == 5
    assert record["assigned_user_id"] == 11
    assert record["priority"] == "urgent"
    assert record["ai_summary"] is None
    assert record["ai_tags"] == []


@pytest.mark.anyio
async def test_list_ticket_assets_formats_records(monkeypatch):
    rows = [
        {
            "asset_id": 5,
            "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            "name": "Device",
            "serial_number": "SER123",
            "status": "online",
            "type": "laptop",
            "os_name": "Windows",
        }
    ]
    dummy_db = _ListTicketAssetsDB(rows)
    monkeypatch.setattr(tickets, "db", dummy_db)

    assets = await tickets.list_ticket_assets(12)

    assert dummy_db.fetch_sql.startswith("SELECT")
    assert dummy_db.fetch_params == (12,)
    assert assets[0]["asset_id"] == 5
    assert assets[0]["name"] == "Device"
    assert assets[0]["serial_number"] == "SER123"
    assert assets[0]["linked_at"].tzinfo is not None


@pytest.mark.anyio
async def test_replace_ticket_assets_updates_links(monkeypatch):
    existing = [{"asset_id": 1}, {"asset_id": 2}]
    final_rows = [
        {
            "asset_id": 2,
            "created_at": None,
            "name": "Device B",
            "serial_number": "B",
            "status": "online",
            "type": "laptop",
            "os_name": "Windows",
        },
        {
            "asset_id": 3,
            "created_at": None,
            "name": "Device C",
            "serial_number": "C",
            "status": "offline",
            "type": "server",
            "os_name": "Linux",
        },
    ]
    dummy_db = _ReplaceTicketAssetsDB(existing, final_rows)
    monkeypatch.setattr(tickets, "db", dummy_db)

    assets = await tickets.replace_ticket_assets(77, [2, 3])

    delete_calls = [call for call in dummy_db.execute_calls if call[0].startswith("DELETE FROM ticket_assets")]
    insert_calls = [call for call in dummy_db.execute_calls if call[0].startswith("INSERT INTO ticket_assets")]

    assert delete_calls
    assert insert_calls
    assert delete_calls[0][1][0] == 77
    assert insert_calls[0][1] == (77, 3)
    assert len(assets) == 2


@pytest.mark.anyio
async def test_create_reply_returns_inserted_record(monkeypatch):
    fetched = {
        "id": 42,
        "ticket_id": 3,
        "author_id": 4,
        "body": "Reply",
        "is_internal": 0,
        "minutes_spent": 15,
        "is_billable": 1,
        "created_at": None,
    }
    dummy_db = _DummyTicketDB(fetched)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.create_reply(
        ticket_id=3,
        author_id=4,
        body="Reply",
        is_internal=False,
    )

    assert record["id"] == 42
    assert record["minutes_spent"] == 15
    assert record["is_billable"] is True
    assert "SELECT tr.*, lt.name AS labour_type_name" in dummy_db.fetch_sql
    assert "FROM ticket_replies tr" in dummy_db.fetch_sql
    assert "LEFT JOIN ticket_labour_types lt ON tr.labour_type_id = lt.id" in dummy_db.fetch_sql
    assert dummy_db.fetch_params == (42,)


@pytest.mark.anyio
async def test_create_reply_falls_back_when_fetch_missing(monkeypatch):
    dummy_db = _DummyTicketDB(fetched_row=None)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.create_reply(
        ticket_id=9,
        author_id=None,
        body="Internal",
        is_internal=True,
    )

    assert record["id"] == 42
    assert record["ticket_id"] == 9
    assert record["author_id"] is None
    assert record["body"] == "Internal"
    assert record["is_internal"] == 1
    assert record["minutes_spent"] is None
    assert record["is_billable"] is False


@pytest.mark.anyio
async def test_get_reply_by_id_fetches_normalised_record(monkeypatch):
    fetched = {
        "id": 15,
        "ticket_id": 2,
        "author_id": 7,
        "body": "Reply",
        "is_internal": 0,
        "minutes_spent": "10",
        "is_billable": 1,
        "created_at": None,
    }
    dummy_db = _FetchReplyDB(fetched)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.get_reply_by_id(15)

    assert "SELECT tr.*, lt.name AS labour_type_name" in dummy_db.fetch_sql
    assert "FROM ticket_replies tr" in dummy_db.fetch_sql
    assert "LEFT JOIN ticket_labour_types lt ON tr.labour_type_id = lt.id" in dummy_db.fetch_sql
    assert dummy_db.fetch_params == (15,)
    assert record["id"] == 15
    assert record["minutes_spent"] == 10
    assert record["is_billable"] is True


@pytest.mark.anyio
async def test_update_reply_updates_minutes_and_billable(monkeypatch):
    fetched = {
        "id": 7,
        "ticket_id": 3,
        "author_id": 4,
        "body": "Reply",
        "is_internal": 0,
        "minutes_spent": 15,
        "is_billable": 1,
        "created_at": None,
    }
    dummy_db = _UpdateTicketDB(fetched)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.update_reply(7, minutes_spent=15, is_billable=True)

    assert "UPDATE ticket_replies" in dummy_db.execute_sql
    assert dummy_db.execute_params == (15, 1, 7)
    assert (
        "SELECT tr.*, lt.name AS labour_type_name, lt.code AS labour_type_code"
        in dummy_db.fetch_sql
    )
    assert "FROM ticket_replies tr" in dummy_db.fetch_sql
    assert "LEFT JOIN ticket_labour_types lt ON tr.labour_type_id = lt.id" in dummy_db.fetch_sql
    assert "WHERE tr.id = %s" in dummy_db.fetch_sql
    assert record["minutes_spent"] == 15
    assert record["is_billable"] is True


@pytest.mark.anyio
async def test_update_reply_clears_minutes(monkeypatch):
    fetched = {
        "id": 9,
        "ticket_id": 5,
        "author_id": 8,
        "body": "Reply",
        "is_internal": 0,
        "minutes_spent": None,
        "is_billable": 0,
        "created_at": None,
    }
    dummy_db = _UpdateTicketDB(fetched)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.update_reply(9, minutes_spent=None)

    assert "minutes_spent = NULL" in dummy_db.execute_sql
    assert dummy_db.execute_params == (9,)
    assert record["minutes_spent"] is None


@pytest.mark.anyio
async def test_get_ticket_by_external_reference(monkeypatch):
    sample_row = {
        "id": 7,
        "company_id": None,
        "requester_id": None,
        "assigned_user_id": None,
        "subject": "Example",
        "description": None,
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "external_reference": "SYNC-1",
        "ai_summary": None,
        "ai_summary_status": None,
        "ai_summary_model": None,
        "ai_resolution_state": None,
        "ai_summary_updated_at": None,
        "ai_tags": "[\"sample\"]",
        "ai_tags_status": None,
        "ai_tags_model": None,
        "ai_tags_updated_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "closed_at": None,
    }
    dummy_db = _UpdateTicketDB(sample_row)
    monkeypatch.setattr(tickets, "db", dummy_db)

    record = await tickets.get_ticket_by_external_reference("SYNC-1")

    assert record is not None
    assert record["id"] == 7
    assert record["ai_tags"] == ["sample"]


@pytest.mark.anyio
async def test_list_tickets_for_user_supports_multiple_statuses(monkeypatch):
    dummy_db = _ListTicketsDB()
    monkeypatch.setattr(tickets, "db", dummy_db)

    await tickets.list_tickets_for_user(
        5,
        status=["waiting_on_client", "pending_client"],
    )

    assert "t.status IN (%s, %s)" in dummy_db.fetch_sql
    assert dummy_db.fetch_params == (
        5,
        5,
        5,
        "waiting_on_client",
        "pending_client",
        25,
        0,
    )


@pytest.mark.anyio
async def test_count_tickets_for_user_supports_multiple_statuses(monkeypatch):
    dummy_db = _CountTicketsDB(count=3)
    monkeypatch.setattr(tickets, "db", dummy_db)

    total = await tickets.count_tickets_for_user(
        8,
        status=("pending_review", "in_progress"),
    )

    assert total == 3
    assert "t.status IN (%s, %s)" in dummy_db.fetch_sql
    assert dummy_db.fetch_params == (
        8,
        8,
        8,
        "pending_review",
        "in_progress",
    )


@pytest.mark.anyio
async def test_update_ticket_allows_updated_at_override(monkeypatch):
    sample_row = {
        "id": 1,
        "company_id": None,
        "requester_id": None,
        "assigned_user_id": None,
        "subject": "Existing",
        "description": None,
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "external_reference": None,
        "ai_summary": None,
        "ai_summary_status": None,
        "ai_summary_model": None,
        "ai_resolution_state": None,
        "ai_summary_updated_at": None,
        "ai_tags": None,
        "ai_tags_status": None,
        "ai_tags_model": None,
        "ai_tags_updated_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "closed_at": None,
    }
    dummy_db = _UpdateTicketDB(sample_row)
    monkeypatch.setattr(tickets, "db", dummy_db)

    override = datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc)
    await tickets.update_ticket(1, status="resolved", updated_at=override)

    assert dummy_db.execute_sql.startswith("UPDATE tickets SET")
    assert dummy_db.execute_params[-1] == 1
    assert dummy_db.execute_params[-2] == override


@pytest.mark.anyio
async def test_delete_tickets_returns_deleted_count(monkeypatch):
    dummy_db = _BulkDeleteDB(count=2)
    monkeypatch.setattr(tickets, "db", dummy_db)

    deleted = await tickets.delete_tickets([1, "2", "ignored", 0, -5, 2])

    assert deleted == 2
    assert dummy_db.fetch_sql.startswith("SELECT COUNT(*) AS total FROM tickets WHERE id IN")
    assert dummy_db.fetch_params == (1, 2)
    assert dummy_db.execute_sql.startswith("DELETE FROM tickets WHERE id IN")
    assert dummy_db.execute_params == (1, 2)


@pytest.mark.anyio
async def test_delete_tickets_ignores_invalid_values(monkeypatch):
    dummy_db = _BulkDeleteDB(count=0)
    monkeypatch.setattr(tickets, "db", dummy_db)

    deleted = await tickets.delete_tickets([None, "", "abc", -1])

    assert deleted == 0
    assert dummy_db.fetch_sql is None
    assert dummy_db.execute_sql is None
