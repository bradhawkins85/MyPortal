from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services import agent as agent_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_execute_agent_query_returns_sources(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {
            "company_id": 1,
            "company_name": "Contoso",
            "can_access_shop": True,
        }
    ]

    kb_result = {
        "results": [
            {
                "slug": "network-guide",
                "title": "Network setup guide",
                "summary": "Step-by-step instructions",
                "excerpt": "Use the documented VLAN layout.",
                "updated_at_iso": "2025-01-05T09:00:00Z",
            }
        ]
    }

    ticket_rows = [
        {
            "id": 42,
            "subject": "Firewall reboot",
            "status": "open",
            "priority": "high",
            "description": "Customer reported outage",
            "updated_at": datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc),
            "company_id": 1,
        }
    ]

    product_rows = [
        {
            "id": 55,
            "name": "Secure Router",
            "sku": "HW-001",
            "vendor_sku": "SR-001",
            "price": Decimal("199.99"),
            "description": "Recommended for branch offices",
            "cross_sell_products": [],
            "upsell_products": [],
        }
    ]

    package_rows = [
        {
            "id": 91,
            "name": "Remote Office Bundle",
            "sku": "PKG-100",
            "description": "Includes workstation, monitors, and accessories",
            "product_count": 4,
        }
    ]

    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "build_access_context",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "search_articles",
        AsyncMock(return_value=kb_result),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo,
        "list_tickets_for_user",
        AsyncMock(return_value=ticket_rows),
    )
    monkeypatch.setattr(
        agent_service.shop_repo,
        "list_products",
        AsyncMock(return_value=product_rows),
    )
    monkeypatch.setattr(
        agent_service.shop_repo,
        "list_packages",
        AsyncMock(return_value=package_rows),
    )

    async def fake_trigger(slug, payload, *, background):
        assert slug == "ollama"
        assert background is False
        prompt = payload.get("prompt")
        assert prompt
        assert "Knowledge base" in prompt
        return {
            "status": "succeeded",
            "model": "llama3",
            "response": {"response": "Answer text"},
            "event_id": 918,
        }

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "network setup",
        user,
        active_company_id=1,
        memberships=memberships,
    )

    assert result["status"] == "succeeded"
    assert result["answer"] == "Answer text"
    assert result["model"] == "llama3"
    assert result["sources"]["knowledge_base"][0]["slug"] == "network-guide"
    assert result["sources"]["tickets"][0]["id"] == 42
    assert result["sources"]["products"][0]["sku"] == "HW-001"
    assert result["sources"]["packages"][0]["sku"] == "PKG-100"
    assert result["context"]["companies"][0]["company_id"] == 1


@pytest.mark.anyio
async def test_execute_agent_query_rejects_blank(monkeypatch):
    result = await agent_service.execute_agent_query("   ", {"id": 1}, memberships=[])
    assert result["status"] == "error"
    assert result["answer"] is None
