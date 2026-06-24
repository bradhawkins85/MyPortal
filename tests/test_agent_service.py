from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
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


@pytest.mark.anyio
async def test_execute_agent_query_has_relevant_sources_flag(monkeypatch):
    """Test that has_relevant_sources flag is set correctly when sources are found."""
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {"company_id": 1, "company_name": "Test Co", "can_access_shop": False}
    ]

    kb_result = {
        "results": [
            {
                "slug": "test-article",
                "title": "Test Article",
                "summary": "Test summary",
                "excerpt": "Test excerpt",
                "updated_at_iso": "2025-01-05T09:00:00Z",
            }
        ]
    }

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
        AsyncMock(return_value=[]),
    )

    async def fake_trigger(slug, payload, *, background):
        return {
            "status": "succeeded",
            "model": "llama3",
            "response": {"response": "Answer text"},
            "event_id": 918,
        }

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "test query",
        user,
        active_company_id=1,
        memberships=memberships,
    )

    assert result["has_relevant_sources"] is True
    assert len(result["sources"]["knowledge_base"]) == 1


@pytest.mark.anyio
async def test_execute_agent_query_no_relevant_sources(monkeypatch):
    """Test that has_relevant_sources flag is False when no sources are found."""
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {"company_id": 1, "company_name": "Test Co", "can_access_shop": False}
    ]

    # Return empty results
    kb_result = {"results": []}

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
        AsyncMock(return_value=[]),
    )

    async def fake_trigger(slug, payload, *, background):
        prompt = payload.get("prompt", "")
        # Verify the prompt contains the updated messaging
        assert (
            "don't have specific information" in prompt.lower()
            or "no portal records matched" in prompt.lower()
        )
        return {
            "status": "succeeded",
            "model": "llama3",
            "response": {"response": "I don't have specific information about that."},
            "event_id": 919,
        }

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "test query",
        user,
        active_company_id=1,
        memberships=memberships,
    )

    assert result["has_relevant_sources"] is False
    assert len(result["sources"]["knowledge_base"]) == 0
    assert len(result["sources"]["tickets"]) == 0
    assert len(result["sources"]["products"]) == 0
    assert len(result["sources"]["packages"]) == 0


@pytest.mark.anyio
async def test_execute_agent_query_includes_chat_order_and_asset_sources(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {
            "company_id": 1,
            "company_name": "VPN Co",
            "can_access_shop": False,
            "can_access_chat": True,
            "can_access_orders": True,
            "can_manage_assets": True,
            "can_manage_issues": True,
            "can_view_m365_user_mailboxes": True,
            "can_view_m365_best_practices": True,
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
        AsyncMock(return_value={"results": []}),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo, "list_tickets_for_user", AsyncMock(return_value=[])
    )

    async def fake_fetch_all(sql, params):
        if "FROM service_status_services" in sql:
            return [
                {
                    "id": 8,
                    "name": "VPN Service",
                    "description": "Remote access platform",
                    "status": "degraded",
                    "status_message": "VPN logins are delayed",
                }
            ]
        if "FROM m365_mailboxes" in sql:
            return [
                {
                    "company_id": 1,
                    "user_principal_name": "vpn.user@example.com",
                    "display_name": "VPN User",
                    "mailbox_type": "UserMailbox",
                    "storage_used_bytes": 1234,
                }
            ]
        if "FROM chat_rooms" in sql:
            return [
                {
                    "id": 11,
                    "subject": "VPN chat",
                    "status": "open",
                    "company_id": 1,
                    "updated_at": datetime(2025, 1, 7, 8, 0, tzinfo=timezone.utc),
                    "linked_ticket_id": 42,
                    "matching_message": "VPN disconnects every hour",
                }
            ]
        if "FROM shop_orders" in sql:
            return [
                {
                    "order_number": "ORD-100",
                    "company_id": 1,
                    "order_date": datetime(2025, 1, 8, 9, 0, tzinfo=timezone.utc),
                    "status": "processing",
                    "shipping_status": "pending",
                    "po_number": "PO-77",
                    "consignment_id": None,
                    "notes": "VPN appliance order",
                    "item_count": 2,
                }
            ]
        if "FROM assets" in sql:
            return [
                {
                    "id": 33,
                    "company_id": 1,
                    "name": "VPN Gateway",
                    "type": "Firewall",
                    "serial_number": "SN123",
                    "status": "active",
                    "os_name": "RouterOS",
                    "last_user": None,
                    "warranty_status": "in_warranty",
                    "last_sync": datetime(2025, 1, 9, 10, 0, tzinfo=timezone.utc),
                }
            ]
        return []

    monkeypatch.setattr(agent_service.db, "fetch_all", fake_fetch_all)

    async def fake_trigger(slug, payload, *, background):
        prompt = payload.get("prompt", "")
        assert "Chats accessible" in prompt
        assert "Orders accessible" in prompt
        assert "Assets accessible" in prompt
        assert "Service statuses accessible" in prompt
        assert "Backup summary jobs accessible" in prompt
        assert "Reports accessible" in prompt
        assert "Office 365 mailboxes accessible" in prompt
        assert "Microsoft 365 best practices accessible" in prompt
        return {
            "status": "succeeded",
            "model": "llama3",
            "response": {"response": "Found sources"},
        }

    issue_overviews = [
        SimpleNamespace(
            issue_id=22,
            name="VPN rollout",
            slug="vpn-rollout",
            description="Track VPN deployment issues",
            updated_at_iso="2025-01-10T10:00:00+00:00",
            assignments=[
                SimpleNamespace(
                    company_id=1,
                    company_name="VPN Co",
                    status="investigating",
                    status_label="Investigating",
                )
            ],
        )
    ]
    monkeypatch.setattr(
        agent_service.issues_service,
        "build_issue_overview",
        AsyncMock(return_value=issue_overviews),
    )
    monkeypatch.setattr(
        agent_service.backup_jobs_service,
        "list_jobs_with_latest",
        AsyncMock(
            return_value=[
                {
                    "id": 44,
                    "company_id": 1,
                    "name": "VPN Config Backup",
                    "description": "Backs up VPN configuration",
                    "latest_status": "pass",
                    "today_status": "pass",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        agent_service.reporting_repo,
        "list_queries_for_user",
        AsyncMock(
            return_value=[
                {
                    "id": 55,
                    "slug": "vpn-report",
                    "name": "VPN Report",
                    "description": "Reports VPN usage",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        agent_service.m365_bp_repo,
        "list_results",
        AsyncMock(
            return_value=[
                {
                    "check_id": "vpn-mfa",
                    "check_name": "VPN MFA Best Practice",
                    "status": "pass",
                    "details": "VPN users require MFA",
                }
            ]
        ),
    )

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "VPN", user, active_company_id=1, memberships=memberships
    )

    assert result["sources"]["companies"][0]["name"] == "VPN Co"
    assert result["sources"]["issues"][0]["id"] == 22
    assert result["sources"]["chats"][0]["id"] == 11
    assert result["sources"]["orders"][0]["order_number"] == "ORD-100"
    assert result["sources"]["assets"][0]["id"] == 33
    assert result["sources"]["service_status"][0]["id"] == 8
    assert result["sources"]["backup_jobs"][0]["id"] == 44
    assert result["sources"]["reports"][0]["key"] == "vpn-report"
    assert result["sources"]["mailboxes"][0]["user_principal_name"] == "vpn.user@example.com"
    assert result["sources"]["best_practices"][0]["check_id"] == "vpn-mfa"
    assert result["has_relevant_sources"] is True


@pytest.mark.anyio
async def test_execute_agent_query_includes_generic_feature_pack_sources(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    user = {"id": 7, "is_super_admin": False}
    memberships = [{"company_id": 1, "company_name": "Contoso"}]
    provider_calls = []

    async def feature_provider(**context):
        provider_calls.append(context)
        return [
            {
                "title": "Backups policy",
                "summary": "Nightly backup completed successfully",
                "url": "/backups/jobs/1",
                "source_type": "backup_job",
                "metadata": {"job_id": 1},
            }
        ]

    module = ModuleType("app.features.backups")
    module.AGENT_SEARCH_PROVIDER = feature_provider
    monkeypatch.setitem(sys.modules, "app.features.backups", module)
    fake_registry = SimpleNamespace(
        _states={"backups": SimpleNamespace(pack=SimpleNamespace(slug="backups"))}
    )
    monkeypatch.setattr(agent_service, "get_registry", lambda: fake_registry)

    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "build_access_context",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "search_articles",
        AsyncMock(return_value={"results": []}),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo, "list_tickets_for_user", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(agent_service.db, "fetch_all", AsyncMock(return_value=[]))

    async def fake_trigger(slug, payload, *, background):
        prompt = payload.get("prompt", "")
        assert "Feature pack results accessible" in prompt
        assert "[Feature:backups] Backups policy" in prompt
        return {"status": "succeeded", "response": {"response": "Found backups"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "backup", user, active_company_id=1, memberships=memberships
    )

    assert provider_calls[0]["user"] == user
    assert provider_calls[0]["company_ids"] == [1]
    assert result["sources"]["feature_packs"]["backups"][0]["title"] == "Backups policy"
    assert result["has_relevant_sources"] is True
