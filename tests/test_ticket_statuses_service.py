from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import ticket_statuses as ticket_status_repo
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_replace_ticket_statuses_normalises_and_persists(monkeypatch):
    captured: list[dict[str, str]] = []

    async def fake_replace_statuses(definitions):
        captured.extend(definitions)
        return [
            {
                "tech_status": item["tech_status"],
                "tech_label": item["tech_label"],
                "public_status": item["public_status"],
            }
            for item in definitions
        ]

    monkeypatch.setattr(ticket_status_repo, "replace_statuses", fake_replace_statuses)

    results = await tickets_service.replace_ticket_statuses(
        [
            {
                "techLabel": "In Progress",
                "publicStatus": "Working",
                "existingSlug": "in_progress",
            },
            {
                "techLabel": "Waiting on Customer",
                "publicStatus": "Awaiting customer",
            },
        ]
    )

    assert [definition.tech_status for definition in results] == [
        "in_progress",
        "waiting_on_customer",
    ]
    assert captured[0]["original_slug"] == "in_progress"
    assert captured[1]["tech_status"] == "waiting_on_customer"


@pytest.mark.anyio
async def test_replace_ticket_statuses_rejects_duplicate_slugs(monkeypatch):
    replace_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ticket_status_repo, "replace_statuses", replace_mock)

    with pytest.raises(ValueError):
        await tickets_service.replace_ticket_statuses(
            [
                {"techLabel": "Pending Vendor", "publicStatus": "Waiting"},
                {"techLabel": "pending_vendor", "publicStatus": "Waiting"},
            ]
        )

    replace_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_validate_status_choice_accepts_existing_status(monkeypatch):
    async def fake_exists(slug: str) -> bool:
        return slug == "waiting_on_vendor"

    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_exists)

    slug = await tickets_service.validate_status_choice("Waiting on vendor")
    assert slug == "waiting_on_vendor"


@pytest.mark.anyio
async def test_validate_status_choice_rejects_unknown_status(monkeypatch):
    async def fake_exists(slug: str) -> bool:
        return False

    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_exists)

    with pytest.raises(ValueError):
        await tickets_service.validate_status_choice("does-not-exist")


@pytest.mark.anyio
async def test_resolve_status_or_default_uses_first_definition(monkeypatch):
    async def fake_exists(slug: str) -> bool:
        return False

    async def fake_list_definitions():
        return [
            tickets_service.TicketStatusDefinition(
                tech_status="queued",
                tech_label="Queued",
                public_status="Queued",
            ),
            tickets_service.TicketStatusDefinition(
                tech_status="on_hold",
                tech_label="On hold",
                public_status="On hold",
            ),
        ]

    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_exists)
    monkeypatch.setattr(tickets_service, "list_status_definitions", fake_list_definitions)

    slug = await tickets_service.resolve_status_or_default(None)
    assert slug == "queued"


def test_slugify_status_label_handles_various_input():
    assert ticket_status_repo.slugify_status_label("In Progress") == "in_progress"
    assert ticket_status_repo.slugify_status_label("  ---  ") == ""
    assert ticket_status_repo.slugify_status_label("Awaiting / Vendor") == "awaiting_vendor"
