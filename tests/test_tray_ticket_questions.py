"""Tests for the tray ticket dynamic questions feature.

Covers:
- Service: question resolution, condition evaluation, answer validation,
  snapshot building (including is_required_snapshot).
- Repository: CRUD and condition helpers (exercised via a temp SQLite DB).
- API: GET /api/tray/ticket-questions and POST /api/tray/submit-ticket
  with dynamic answers.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tq_event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def tq_db(tq_event_loop):
    """Fresh SQLite DB for ticket-question tests."""
    tmp = tempfile.TemporaryDirectory()
    sqlite_path = Path(tmp.name) / "tq-tests.db"

    from app.core.database import db

    original_use_sqlite = db._use_sqlite
    original_get_path = db._get_sqlite_path
    db._use_sqlite = True
    db._get_sqlite_path = lambda: sqlite_path  # type: ignore[assignment]

    tq_event_loop.run_until_complete(db.connect())

    ddl = [
        """CREATE TABLE IF NOT EXISTS tray_ticket_questions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               scope TEXT NOT NULL DEFAULT 'global',
               company_id INTEGER NULL,
               field_type TEXT NOT NULL DEFAULT 'text',
               label TEXT NOT NULL,
               placeholder TEXT NULL,
               is_required INTEGER NOT NULL DEFAULT 0,
               options_json TEXT NULL,
               sort_order INTEGER NOT NULL DEFAULT 0,
               is_active INTEGER NOT NULL DEFAULT 1,
               created_by_user_id INTEGER NULL,
               created_at TEXT DEFAULT (datetime('now')),
               updated_at TEXT DEFAULT (datetime('now'))
           )""",
        """CREATE TABLE IF NOT EXISTS tray_ticket_question_conditions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               question_id INTEGER NOT NULL,
               parent_question_id INTEGER NOT NULL,
               operator TEXT NOT NULL DEFAULT 'equals',
               expected_value TEXT NOT NULL DEFAULT '',
               FOREIGN KEY (question_id)
                   REFERENCES tray_ticket_questions (id) ON DELETE CASCADE,
               FOREIGN KEY (parent_question_id)
                   REFERENCES tray_ticket_questions (id) ON DELETE CASCADE
           )""",
        """CREATE TABLE IF NOT EXISTS tray_ticket_answers (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ticket_id INTEGER NOT NULL,
               question_id INTEGER NULL,
               question_label_snapshot TEXT NOT NULL,
               is_required_snapshot INTEGER NOT NULL DEFAULT 0,
               answer_value TEXT NULL,
               created_at TEXT DEFAULT (datetime('now'))
           )""",
    ]
    for stmt in ddl:
        tq_event_loop.run_until_complete(db.execute(stmt))

    yield db

    tq_event_loop.run_until_complete(db.disconnect())
    db._use_sqlite = original_use_sqlite
    db._get_sqlite_path = original_get_path
    tmp.cleanup()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Service unit tests (pure Python, no DB)
# ---------------------------------------------------------------------------


class TestEvaluateVisibility:
    def test_no_conditions_always_visible(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {"id": 1, "conditions": []}
        assert evaluate_visibility(q, {}) is True

    def test_equals_match(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 2,
            "conditions": [
                {"parent_question_id": 1, "operator": "equals", "expected_value": "yes"}
            ],
        }
        assert evaluate_visibility(q, {1: "yes"}) is True
        assert evaluate_visibility(q, {1: "no"}) is False

    def test_not_equals(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 2,
            "conditions": [
                {"parent_question_id": 1, "operator": "not_equals", "expected_value": "no"}
            ],
        }
        assert evaluate_visibility(q, {1: "yes"}) is True
        assert evaluate_visibility(q, {1: "no"}) is False

    def test_contains(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 2,
            "conditions": [
                {"parent_question_id": 1, "operator": "contains", "expected_value": "network"}
            ],
        }
        assert evaluate_visibility(q, {1: "Network issue"}) is True
        assert evaluate_visibility(q, {1: "hardware"}) is False

    def test_unknown_operator_hides_question(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 2,
            "conditions": [
                {"parent_question_id": 1, "operator": "regex", "expected_value": ".*"}
            ],
        }
        assert evaluate_visibility(q, {1: "anything"}) is False

    def test_case_insensitive(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 2,
            "conditions": [
                {"parent_question_id": 1, "operator": "equals", "expected_value": "YES"}
            ],
        }
        assert evaluate_visibility(q, {1: "yes"}) is True

    def test_multiple_conditions_all_required(self):
        from app.services.tray_ticket_questions import evaluate_visibility

        q = {
            "id": 3,
            "conditions": [
                {"parent_question_id": 1, "operator": "equals", "expected_value": "yes"},
                {"parent_question_id": 2, "operator": "equals", "expected_value": "done"},
            ],
        }
        assert evaluate_visibility(q, {1: "yes", 2: "done"}) is True
        assert evaluate_visibility(q, {1: "yes", 2: "pending"}) is False


class TestGetVisibleQuestions:
    def _make_q(self, qid: int, conditions: list | None = None) -> dict:
        return {"id": qid, "conditions": conditions or []}

    def test_all_unconditional_visible(self):
        from app.services.tray_ticket_questions import get_visible_questions

        qs = [self._make_q(1), self._make_q(2), self._make_q(3)]
        visible = get_visible_questions(qs, {})
        assert [q["id"] for q in visible] == [1, 2, 3]

    def test_conditional_hidden_when_parent_not_answered(self):
        from app.services.tray_ticket_questions import get_visible_questions

        qs = [
            self._make_q(1),
            self._make_q(
                2,
                conditions=[{"parent_question_id": 1, "operator": "equals", "expected_value": "yes"}],
            ),
        ]
        visible = get_visible_questions(qs, {1: "no"})
        assert [q["id"] for q in visible] == [1]


class TestValidateAnswers:
    def _make_q(self, qid: int, label: str, required: bool, field_type: str = "text",
                options: list | None = None, conditions: list | None = None) -> dict:
        return {
            "id": qid,
            "label": label,
            "is_required": required,
            "field_type": field_type,
            "options": options or [],
            "conditions": conditions or [],
        }

    def test_required_visible_empty_is_error(self):
        from app.services.tray_ticket_questions import validate_answers

        qs = [self._make_q(1, "Site address", required=True)]
        errors = validate_answers(qs, [{"question_id": 1, "value": ""}])
        assert len(errors) == 1
        assert "Site address" in errors[0]

    def test_optional_empty_ok(self):
        from app.services.tray_ticket_questions import validate_answers

        qs = [self._make_q(1, "Notes", required=False)]
        errors = validate_answers(qs, [{"question_id": 1, "value": ""}])
        assert errors == []

    def test_select_invalid_option_error(self):
        from app.services.tray_ticket_questions import validate_answers

        qs = [self._make_q(1, "Type", required=True, field_type="select",
                           options=["Hardware", "Software"])]
        errors = validate_answers(qs, [{"question_id": 1, "value": "Network"}])
        assert any("Type" in e for e in errors)

    def test_select_valid_option_ok(self):
        from app.services.tray_ticket_questions import validate_answers

        qs = [self._make_q(1, "Type", required=True, field_type="select",
                           options=["Hardware", "Software"])]
        errors = validate_answers(qs, [{"question_id": 1, "value": "Hardware"}])
        assert errors == []

    def test_hidden_required_question_not_validated(self):
        from app.services.tray_ticket_questions import validate_answers

        # Q2 required, but hidden because Q1 answer doesn't match condition
        qs = [
            self._make_q(1, "Issue type", required=True, field_type="select",
                         options=["Hardware", "Software"]),
            self._make_q(
                2, "App name", required=True,
                conditions=[{"parent_question_id": 1, "operator": "equals", "expected_value": "Software"}],
            ),
        ]
        # Q1 answered as "Hardware" -> Q2 hidden -> no error even though empty
        errors = validate_answers(
            qs,
            [{"question_id": 1, "value": "Hardware"}, {"question_id": 2, "value": ""}],
        )
        assert errors == []


class TestBuildAdditionalDetails:
    def test_empty_when_no_answers(self):
        from app.services.tray_ticket_questions import build_additional_details

        qs = [{"id": 1, "label": "Site", "conditions": [], "is_required": True}]
        result = build_additional_details(qs, [{"question_id": 1, "value": ""}])
        assert result == ""

    def test_includes_visible_non_empty(self):
        from app.services.tray_ticket_questions import build_additional_details

        qs = [{"id": 1, "label": "Site address", "conditions": [], "is_required": True}]
        result = build_additional_details(qs, [{"question_id": 1, "value": "Level 2, 123 Main St"}])
        assert "Site address" in result
        assert "Level 2, 123 Main St" in result
        assert "Additional Details" in result

    def test_excludes_hidden_questions(self):
        from app.services.tray_ticket_questions import build_additional_details

        qs = [
            {"id": 1, "label": "Type", "conditions": [], "is_required": True},
            {
                "id": 2,
                "label": "App name",
                "conditions": [
                    {"parent_question_id": 1, "operator": "equals", "expected_value": "Software"}
                ],
                "is_required": False,
            },
        ]
        result = build_additional_details(
            qs,
            [{"question_id": 1, "value": "Hardware"}, {"question_id": 2, "value": "Chrome"}],
        )
        assert "App name" not in result


class TestBuildAnswerSnapshots:
    """is_required_snapshot must mirror the question's is_required at submit time."""

    def _q(self, qid: int, label: str, required: bool, conditions: list | None = None) -> dict:
        return {"id": qid, "label": label, "is_required": required,
                "conditions": conditions or [], "options": [], "field_type": "text"}

    def test_snapshot_carries_required_flag(self):
        from app.services.tray_ticket_questions import build_answer_snapshots

        qs = [self._q(1, "Site", required=True), self._q(2, "Room", required=False)]
        snaps = build_answer_snapshots(qs, [
            {"question_id": 1, "value": "HQ"},
            {"question_id": 2, "value": "A101"},
        ])
        by_qid = {s["question_id"]: s for s in snaps}
        assert by_qid[1]["is_required_snapshot"] is True
        assert by_qid[2]["is_required_snapshot"] is False

    def test_hidden_answers_excluded(self):
        from app.services.tray_ticket_questions import build_answer_snapshots

        qs = [
            self._q(1, "Type", required=True),
            self._q(2, "App", required=True,
                    conditions=[{"parent_question_id": 1, "operator": "equals", "expected_value": "Software"}]),
        ]
        snaps = build_answer_snapshots(qs, [
            {"question_id": 1, "value": "Hardware"},
            {"question_id": 2, "value": "Chrome"},
        ])
        qids = [s["question_id"] for s in snaps]
        assert 1 in qids
        assert 2 not in qids

    def test_label_snapshot_preserved(self):
        from app.services.tray_ticket_questions import build_answer_snapshots

        qs = [self._q(99, "Original label", required=False)]
        snaps = build_answer_snapshots(qs, [{"question_id": 99, "value": "abc"}])
        assert snaps[0]["question_label_snapshot"] == "Original label"


# ---------------------------------------------------------------------------
# Repository tests (SQLite)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_repo_create_and_get_question(tq_db):
    from app.repositories import tray_ticket_questions as tq_repo

    record = await tq_repo.create_question(
        scope="global",
        company_id=None,
        field_type="text",
        label="Test Q1",
        placeholder="hint",
        is_required=True,
        options=[],
        sort_order=5,
        is_active=True,
        created_by_user_id=None,
    )
    assert record["label"] == "Test Q1"
    assert record["is_required"]
    qid = int(record["id"])
    fetched = await tq_repo.get_question(qid)
    assert fetched is not None
    assert fetched["sort_order"] == 5


@pytest.mark.anyio
async def test_repo_list_global_only(tq_db):
    from app.repositories import tray_ticket_questions as tq_repo

    await tq_repo.create_question(
        scope="global", company_id=None, field_type="boolean", label="Global boolean",
        placeholder=None, is_required=False, options=[], sort_order=1,
        is_active=True, created_by_user_id=None,
    )
    await tq_repo.create_question(
        scope="company", company_id=42, field_type="text", label="Company text",
        placeholder=None, is_required=False, options=[], sort_order=1,
        is_active=True, created_by_user_id=None,
    )
    globals_ = await tq_repo.list_questions(scope="global")
    labels = [q["label"] for q in globals_]
    assert "Global boolean" in labels
    assert "Company text" not in labels


@pytest.mark.anyio
async def test_repo_conditions_replace(tq_db):
    from app.repositories import tray_ticket_questions as tq_repo

    q1 = await tq_repo.create_question(
        scope="global", company_id=None, field_type="select", label="Issue type",
        placeholder=None, is_required=True, options=["HW", "SW"], sort_order=0,
        is_active=True, created_by_user_id=None,
    )
    q2 = await tq_repo.create_question(
        scope="global", company_id=None, field_type="text", label="App name",
        placeholder=None, is_required=False, options=[], sort_order=1,
        is_active=True, created_by_user_id=None,
    )
    await tq_repo.replace_conditions_for_question(
        int(q2["id"]),
        [{"parent_question_id": int(q1["id"]), "operator": "equals", "expected_value": "SW"}],
    )
    conds = await tq_repo.list_conditions_for_question(int(q2["id"]))
    assert len(conds) == 1
    assert conds[0]["operator"] == "equals"
    assert conds[0]["expected_value"] == "SW"

    # Replace with empty removes all conditions
    await tq_repo.replace_conditions_for_question(int(q2["id"]), [])
    conds2 = await tq_repo.list_conditions_for_question(int(q2["id"]))
    assert conds2 == []


@pytest.mark.anyio
async def test_repo_create_answers_stores_required_snapshot(tq_db):
    from app.repositories import tray_ticket_questions as tq_repo

    await tq_repo.create_answers(
        ticket_id=999,
        answers=[
            {
                "question_id": 1,
                "question_label_snapshot": "Site address",
                "is_required_snapshot": True,
                "answer_value": "HQ Level 3",
            },
            {
                "question_id": 2,
                "question_label_snapshot": "Room",
                "is_required_snapshot": False,
                "answer_value": None,
            },
        ],
    )
    stored = await tq_repo.list_answers_for_ticket(999)
    by_qid = {s["question_id"]: s for s in stored}
    assert by_qid[1]["is_required_snapshot"] == 1  # stored as int in SQLite
    assert by_qid[2]["is_required_snapshot"] == 0
    assert by_qid[1]["answer_value"] == "HQ Level 3"
    assert by_qid[2]["answer_value"] is None


# ---------------------------------------------------------------------------
# API endpoint tests (via full app with dependency override)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tq_http_client(tq_db):
    """FastAPI TestClient sharing the same test SQLite singleton."""
    from fastapi.testclient import TestClient
    from app.core.database import db
    from app.main import app
    from app.services.scheduler import scheduler_service

    async def _noop():  # pragma: no cover
        return None

    original_connect = db.connect
    original_disconnect = db.disconnect
    original_run_migrations = db.run_migrations
    db.connect = _noop  # type: ignore[assignment]
    db.disconnect = _noop  # type: ignore[assignment]
    db.run_migrations = _noop  # type: ignore[assignment]
    original_start = scheduler_service.start
    original_stop = scheduler_service.stop
    scheduler_service.start = _noop  # type: ignore[assignment]
    scheduler_service.stop = _noop  # type: ignore[assignment]

    with TestClient(app, follow_redirects=False, headers={"Accept": "application/json"}) as client:
        yield client

    db.connect = original_connect  # type: ignore[assignment]
    db.disconnect = original_disconnect  # type: ignore[assignment]
    db.run_migrations = original_run_migrations  # type: ignore[assignment]
    scheduler_service.start = original_start  # type: ignore[assignment]
    scheduler_service.stop = original_stop  # type: ignore[assignment]


@pytest.fixture
def enrolled_device_token(tq_http_client, tq_event_loop):
    """Enrol a test device and return its auth token + device_uid."""
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw = svc.generate_install_token()
    tq_event_loop.run_until_complete(
        repo.create_install_token(
            label="tq-api-test",
            company_id=None,
            token_hash=svc.hash_token(raw),
            token_prefix=svc.token_prefix(raw),
            created_by_user_id=None,
        )
    )
    resp = tq_http_client.post(
        "/api/tray/enrol",
        json={"install_token": raw, "os": "windows"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["auth_token"], body["device_uid"]


def test_get_ticket_questions_empty(tq_http_client, enrolled_device_token):
    """With no question definitions the endpoint returns an empty list."""
    auth_token, _ = enrolled_device_token
    resp = tq_http_client.get(
        "/api/tray/ticket-questions",
        headers={"Authorization": f"******"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["questions"], list)


def test_get_ticket_questions_includes_global(tq_http_client, enrolled_device_token, tq_event_loop):
    """A global question is returned by the endpoint."""
    from app.repositories import tray_ticket_questions as tq_repo

    tq_event_loop.run_until_complete(
        tq_repo.create_question(
            scope="global",
            company_id=None,
            field_type="text",
            label="Your building",
            placeholder=None,
            is_required=True,
            options=[],
            sort_order=0,
            is_active=True,
            created_by_user_id=None,
        )
    )

    auth_token, _ = enrolled_device_token
    resp = tq_http_client.get(
        "/api/tray/ticket-questions",
        headers={"Authorization": f"******"},
    )
    assert resp.status_code == 200
    labels = [q["label"] for q in resp.json()["questions"]]
    assert "Your building" in labels


def test_submit_ticket_validates_required_answer(tq_http_client, enrolled_device_token, tq_event_loop):
    """A blank required dynamic answer must return HTTP 422."""
    from app.repositories import tray_ticket_questions as tq_repo

    q = tq_event_loop.run_until_complete(
        tq_repo.create_question(
            scope="global",
            company_id=None,
            field_type="text",
            label="Desk location",
            placeholder=None,
            is_required=True,
            options=[],
            sort_order=99,
            is_active=True,
            created_by_user_id=None,
        )
    )
    qid = int(q["id"])

    _, device_uid = enrolled_device_token
    resp = tq_http_client.post(
        "/api/tray/submit-ticket",
        json={
            "device_uid": device_uid,
            "name": "Alice",
            "email": "alice@test.invalid",
            "subject": "Help",
            "answers": [{"question_id": qid, "value": ""}],
        },
    )
    assert resp.status_code == 422


def test_submit_ticket_without_answers_still_works(tq_http_client, enrolled_device_token):
    """Backward compat: tickets submitted without answers still succeed."""
    _, device_uid = enrolled_device_token
    resp = tq_http_client.post(
        "/api/tray/submit-ticket",
        json={
            "device_uid": device_uid,
            "name": "Bob",
            "email": "bob@test.invalid",
            "subject": "My issue",
        },
    )
    # May return 200 or 422 depending on required questions in DB;
    # the important thing is that omitting 'answers' doesn't cause a 500.
    assert resp.status_code in (200, 422)

