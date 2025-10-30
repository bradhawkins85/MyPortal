import pytest

from app.repositories import issues as issues_repo


class _ListIssuesDB:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_calls = []

    async def fetch_all(self, sql, params):
        self.fetch_calls.append((sql.strip(), params))
        return self.rows


class _AssignIssueDB:
    def __init__(self, row):
        self.execute_calls = []
        self.fetch_row = row

    async def execute(self, sql, params):
        self.execute_calls.append((sql.strip(), params))

    async def fetch_one(self, sql, params):
        return self.fetch_row


class _CreateIssueDB:
    def __init__(self, row):
        self.insert_calls = []
        self.fetch_calls = []
        self.row = row

    async def execute_returning_lastrowid(self, sql, params):
        self.insert_calls.append((sql.strip(), params))
        return 7

    async def fetch_one(self, sql, params):
        self.fetch_calls.append((sql.strip(), params))
        return self.row


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_issues_with_assignments_groups_records(monkeypatch):
    rows = [
        {
            "issue_id": 5,
            "name": "Network outage",
            "description": "WAN connectivity unstable",
            "assignment_id": 11,
            "company_id": 3,
            "company_name": "Acme",
            "status": "investigating",
            "assignment_created_at_utc": None,
            "assignment_updated_at_utc": None,
            "assignment_updated_by": None,
        },
        {
            "issue_id": 5,
            "name": "Network outage",
            "description": "WAN connectivity unstable",
            "assignment_id": 12,
            "company_id": 4,
            "company_name": "Contoso",
            "status": "new",
            "assignment_created_at_utc": None,
            "assignment_updated_at_utc": None,
            "assignment_updated_by": None,
        },
    ]
    dummy_db = _ListIssuesDB(rows)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    result = await issues_repo.list_issues_with_assignments()

    assert len(result) == 1
    issue = result[0]
    assert issue["issue_id"] == 5
    assert issue["name"] == "Network outage"
    assert len(issue["assignments"]) == 2
    companies = {assignment["company_name"] for assignment in issue["assignments"]}
    assert companies == {"Acme", "Contoso"}


@pytest.mark.anyio
async def test_create_issue_returns_fallback_when_fetch_missing(monkeypatch):
    dummy_db = _CreateIssueDB(row=None)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    created = await issues_repo.create_issue(name="Printer", description="Paper jams", created_by=2)

    assert created["issue_id"] == 7
    assert created["name"] == "Printer"
    assert created["description"] == "Paper jams"
    assert dummy_db.insert_calls


@pytest.mark.anyio
async def test_assign_issue_to_company_returns_normalised_record(monkeypatch):
    row = {
        "assignment_id": 10,
        "issue_id": 5,
        "company_id": 3,
        "status": "resolved",
        "notes": None,
        "updated_by": 2,
        "created_at_utc": None,
        "updated_at_utc": None,
    }
    dummy_db = _AssignIssueDB(row)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    assignment = await issues_repo.assign_issue_to_company(
        issue_id=5,
        company_id=3,
        status="resolved",
        updated_by=2,
    )

    assert assignment["assignment_id"] == 10
    assert assignment["status"] == "resolved"
    assert dummy_db.execute_calls


@pytest.mark.anyio
async def test_update_assignment_status_fetches_updated_record(monkeypatch):
    row = {
        "assignment_id": 12,
        "issue_id": 5,
        "company_id": 4,
        "status": "monitoring",
        "notes": None,
        "updated_by": 9,
        "created_at_utc": None,
        "updated_at_utc": None,
    }
    dummy_db = _AssignIssueDB(row)
    monkeypatch.setattr(issues_repo, "db", dummy_db)

    updated = await issues_repo.update_assignment_status(12, status="monitoring", updated_by=9)

    assert updated["assignment_id"] == 12
    assert updated["status"] == "monitoring"
    assert dummy_db.execute_calls
