"""Regression tests for the in-app user feedback endpoint (``POST /api/feedback``).

The endpoint is a self-contained JSON route that creates a follow-up support
ticket, persists a ``user_feedback`` row and records an audit event.

These tests mount only the feedback router on an isolated ``FastAPI`` app (no
full ``app.main`` lifespan, so no database is required) and mock the
ticket/audit/repository service layer to verify the handler's control flow:

* unauthenticated requests are rejected (``401``);
* ``GET`` is not allowed on the route (``405``);
* a ``down`` rating with no reason/suggestion is rejected (``422``) and creates
  no ticket;
* a valid ``up`` rating creates the ticket + feedback row + audit event
  (``201``) with the correct arguments;
* a ticket-creation failure surfaces as a ``500``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_deps
from app.api.dependencies import database as db_deps
from app.api.routes import feedback as feedback_routes
from app.repositories import user_feedback as user_feedback_repo
from app.services import audit as audit_service
from app.services import tickets as tickets_service

FAKE_USER = {"id": 5, "company_id": "9"}


@pytest.fixture
def feedback_client():
    """Isolated app with only the feedback router (no DB / no full lifespan)."""
    app = FastAPI()
    app.include_router(feedback_routes.router)
    with TestClient(app) as client:
        yield app, client


def _authenticate(app) -> None:
    app.dependency_overrides[auth_deps.get_current_user] = lambda: FAKE_USER
    app.dependency_overrides[db_deps.require_database] = lambda: None


def test_unauthenticated_returns_401(feedback_client):
    _, client = feedback_client
    response = client.post("/api/feedback", json={"rating": "down", "reason": "x"})
    assert response.status_code == 401


def test_wrong_method_returns_405(feedback_client):
    _, client = feedback_client
    assert client.get("/api/feedback").status_code == 405


def test_invalid_rating_rejected(feedback_client):
    app, client = feedback_client
    _authenticate(app)
    try:
        response = client.post("/api/feedback", json={"rating": "bogus", "reason": "x"})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_down_without_reason_returns_422_and_no_ticket(feedback_client):
    app, client = feedback_client
    _authenticate(app)
    try:
        with patch.object(tickets_service, "create_ticket", new=AsyncMock()) as ct:
            response = client.post(
                "/api/feedback",
                json={
                    "rating": "down",
                    "reason": "   ",
                    "suggested_improvements": None,
                    "page_url": None,
                },
            )
        assert response.status_code == 422
        assert ct.called is False  # validation failure must not open a ticket
    finally:
        app.dependency_overrides.clear()


def test_up_creates_ticket_feedback_and_audit(feedback_client):
    app, client = feedback_client
    _authenticate(app)
    try:
        ct = AsyncMock(return_value={"id": 4242})
        cfb = AsyncMock(return_value=777)
        rec = AsyncMock(return_value=None)
        rso = AsyncMock(return_value="new")
        with (
            patch.object(tickets_service, "create_ticket", ct),
            patch.object(tickets_service, "resolve_status_or_default", rso),
            patch.object(user_feedback_repo, "create_feedback", cfb),
            patch.object(audit_service, "record", rec),
        ):
            response = client.post(
                "/api/feedback",
                json={"rating": "up", "reason": "love it", "suggested_improvements": None, "page_url": "/x"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["ok"] is True
        assert body["ticket_id"] == 4242
        assert body["user_feedback_id"] == 777

        ct_kwargs = ct.call_args.kwargs
        assert ct_kwargs["requester_id"] == 5
        assert ct_kwargs["company_id"] == 9  # string "9" coerced to int
        assert ct_kwargs["initial_reply_author_id"] == 5  # not orphaned
        assert ct_kwargs["priority"] == "normal"
        assert ct_kwargs["trigger_automations"] is False
        assert ct_kwargs["send_creation_notification"] is False

        assert rec.call_args.kwargs["action"] == "feedback.submit"
        assert rec.call_args.kwargs["entity_type"] == "ticket"
        assert rec.call_args.kwargs["entity_id"] == 4242

        assert cfb.call_args.kwargs["ticket_id"] == 4242
        assert cfb.call_args.kwargs["rating"] == "up"
        assert cfb.call_args.kwargs["page_url"] == "/x"
    finally:
        app.dependency_overrides.clear()


def test_ticket_creation_failure_returns_500(feedback_client):
    app, client = feedback_client
    _authenticate(app)
    try:
        with (
            patch.object(
                tickets_service,
                "create_ticket",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(
                tickets_service,
                "resolve_status_or_default",
                new=AsyncMock(return_value="new"),
            ),
        ):
            response = client.post(
                "/api/feedback",
                json={"rating": "down", "reason": "real reason", "suggested_improvements": None, "page_url": None},
            )
        assert response.status_code == 500
    finally:
        app.dependency_overrides.clear()
