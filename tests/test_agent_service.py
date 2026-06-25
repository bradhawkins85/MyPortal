from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import agent as agent_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def default_agent_rag_mocks(monkeypatch):
    monkeypatch.setattr(
        agent_service.rag_index_service,
        "index_agent_sources",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        agent_service.rag_retrieval,
        "retrieve_candidates",
        AsyncMock(return_value=[]),
    )


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
        assert "No relevant RAG evidence was found." in prompt
        assert "Knowledge base articles:" not in prompt
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

    assert result["has_relevant_sources"] is False
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
        assert "No relevant RAG evidence was found." in prompt
        assert "Knowledge base articles:" not in prompt
        assert "Companies available to the user:" not in prompt
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
        assert "No relevant RAG evidence was found." in prompt
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
    assert (
        result["sources"]["mailboxes"][0]["user_principal_name"]
        == "vpn.user@example.com"
    )
    assert result["sources"]["best_practices"][0]["check_id"] == "vpn-mfa"
    assert result["has_relevant_sources"] is False


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
        assert "No relevant RAG evidence was found." in prompt
        return {"status": "succeeded", "response": {"response": "Found backups"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "backup", user, active_company_id=1, memberships=memberships
    )

    assert provider_calls[0]["user"] == user
    assert provider_calls[0]["company_ids"] == [1]
    assert result["sources"]["feature_packs"]["backups"][0]["title"] == "Backups policy"
    assert result["has_relevant_sources"] is False


@pytest.mark.anyio
async def test_search_staff_sources_matches_mobile_org_and_custom_fields(monkeypatch):
    memberships = [
        {
            "company_id": 1,
            "company_name": "Contoso",
            "can_manage_staff": True,
            "staff_permission": 3,
        }
    ]
    staff_rows = [
        {
            "id": 101,
            "company_id": 1,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "mobile_phone": "+61 400 555 123",
            "department": "Engineering",
            "job_title": "Principal Analyst",
            "org_company": "Research Org",
            "manager_name": "Grace Hopper",
            "account_action": "create",
            "enabled": True,
            "is_ex_staff": False,
            "onboarding_status": "pending",
            "updated_at": "2026-01-02T03:04:05+00:00",
        }
    ]

    monkeypatch.setattr(
        agent_service.staff_repo, "list_staff", AsyncMock(return_value=staff_rows)
    )
    monkeypatch.setattr(
        agent_service.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={101: {"employment_type": "Contractor", "site": "HQ"}}),
    )

    mobile_matches = await agent_service._search_staff_sources(
        "555 123", memberships=memberships, is_super_admin=False
    )
    org_matches = await agent_service._search_staff_sources(
        "Research Org", memberships=memberships, is_super_admin=False
    )
    custom_matches = await agent_service._search_staff_sources(
        "Contractor", memberships=memberships, is_super_admin=False
    )

    assert mobile_matches[0]["mobile_phone"] == "+61 400 555 123"
    assert org_matches[0]["org_company"] == "Research Org"
    assert custom_matches[0]["custom_fields"] == {
        "employment_type": "Contractor",
        "site": "HQ",
    }


@pytest.mark.anyio
async def test_execute_agent_query_minimises_llm_prompt_context(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {"company_id": 1, "company_name": "Visible Co", "can_access_shop": True},
        {"company_id": 2, "company_name": "Extra Co 2", "can_access_shop": True},
        {"company_id": 3, "company_name": "Extra Co 3", "can_access_shop": True},
        {"company_id": 4, "company_name": "Extra Co 4", "can_access_shop": True},
    ]
    kb_result = {
        "results": [
            {
                "slug": f"kb-{index}",
                "title": f"KB {index}",
                "summary": f"Knowledge base summary {index}",
                "excerpt": f"Knowledge base excerpt {index}",
                "updated_at_iso": "2025-01-05T09:00:00Z",
            }
            for index in range(1, 7)
        ]
    }
    ticket_rows = [
        {
            "id": index,
            "subject": f"Ticket {index}",
            "status": "open",
            "priority": "normal",
            "description": f"Ticket detail {index}",
            "updated_at": datetime(2025, 1, index, tzinfo=timezone.utc),
            "company_id": 1,
        }
        for index in range(1, 7)
    ]
    product_rows = [
        {
            "id": index,
            "name": f"Product {index}",
            "sku": f"SKU-{index}",
            "vendor_sku": None,
            "price": Decimal("1.00"),
            "description": f"Product detail {index}",
            "cross_sell_products": [],
            "upsell_products": [],
        }
        for index in range(1, 7)
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
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(agent_service.db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agent_service.rag_index_service,
        "index_agent_sources",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        agent_service.rag_retrieval,
        "retrieve_candidates",
        AsyncMock(return_value=[]),
    )

    captured_prompt = ""

    async def fake_trigger(slug, payload, *, background):
        nonlocal captured_prompt
        captured_prompt = payload.get("prompt", "")
        return {"status": "succeeded", "response": {"response": "Minimised"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "network", user, active_company_id=1, memberships=memberships
    )

    assert len(result["sources"]["knowledge_base"]) == 6
    assert len(result["sources"]["tickets"]) == 6
    assert len(result["sources"]["products"]) == 6
    assert "No relevant RAG evidence was found." in captured_prompt
    assert "Knowledge base articles:" not in captured_prompt
    assert "Tickets created by or watched by the user:" not in captured_prompt
    assert "Products and hardware recommendations" not in captured_prompt
    assert "Companies available to the user:" not in captured_prompt
    assert "Extra Co" not in captured_prompt


@pytest.mark.anyio
async def test_execute_agent_query_ticket_id_prompt_uses_direct_ticket_only(
    monkeypatch,
):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {"company_id": 1, "company_name": "Visible Co", "can_access_shop": True}
    ]
    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "build_access_context",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        agent_service.knowledge_base_service,
        "search_articles",
        AsyncMock(
            return_value={
                "results": [
                    {
                        "slug": "touchpad",
                        "title": "Touchpad",
                        "summary": "Unrelated",
                        "excerpt": "Unrelated",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo,
        "get_ticket",
        AsyncMock(
            return_value={
                "id": 24425,
                "company_id": 1,
                "requester_id": 7,
                "subject": "CMOS battery purchase",
                "description": "Purchase CR2032 CMOS batteries from Trello card",
            }
        ),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo,
        "list_replies",
        AsyncMock(
            return_value=[
                {"body": "Brad Hawkins asked Jimmi Nolan to source CMOS batteries."}
            ]
        ),
    )
    monkeypatch.setattr(
        agent_service.tickets_repo, "list_tickets_for_user", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        agent_service.shop_repo,
        "list_products",
        AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "name": "Lenovo mouse",
                    "sku": "MOUSE",
                    "price": Decimal("1"),
                    "description": "Unrelated",
                    "cross_sell_products": [],
                    "upsell_products": [],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        agent_service.shop_repo, "list_packages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(agent_service.db, "fetch_all", AsyncMock(return_value=[]))

    captured_prompt = ""

    async def fake_trigger(slug, payload, *, background):
        nonlocal captured_prompt
        captured_prompt = payload.get("prompt", "")
        return {"status": "succeeded", "response": {"response": "Ticket found"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "CMOS batteries purchase Trello card 24425", user, memberships=memberships
    )

    assert result["has_relevant_sources"] is True
    assert "[Ticket:#24425]" in captured_prompt
    assert "CMOS battery purchase" in captured_prompt
    assert "Companies available to the user:" not in captured_prompt
    assert "Knowledge base articles:" not in captured_prompt
    assert "Products and hardware recommendations" not in captured_prompt
    assert "Lenovo mouse" not in captured_prompt
    assert "Touchpad" not in captured_prompt


@pytest.mark.anyio
async def test_execute_agent_query_product_rag_allowlist_excludes_unrelated_products(
    monkeypatch,
):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {"company_id": 1, "company_name": "Visible Co", "can_access_shop": True}
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
    monkeypatch.setattr(
        agent_service.shop_repo, "list_products", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        agent_service.shop_repo, "list_packages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(agent_service.db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agent_service.rag_retrieval,
        "retrieve_candidates",
        AsyncMock(
            return_value=[
                {
                    "source_type": "products",
                    "source_id": "CR2032",
                    "title": "CR2032 CMOS battery",
                    "excerpt": "Compatible CMOS battery",
                    "score": 0.9,
                },
                {
                    "source_type": "products",
                    "source_id": "MOUSE",
                    "title": "Lenovo mouse",
                    "excerpt": "Mouse",
                    "score": 0.2,
                },
                {
                    "source_type": "knowledge_base",
                    "source_id": "touchpad",
                    "title": "Touchpad issue",
                    "excerpt": "Touchpad",
                    "score": 0.1,
                },
            ]
        ),
    )

    captured_prompt = ""

    async def fake_trigger(slug, payload, *, background):
        nonlocal captured_prompt
        captured_prompt = payload.get("prompt", "")
        return {"status": "succeeded", "response": {"response": "Product found"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "show me compatible CMOS batteries", user, memberships=memberships
    )

    assert result["has_relevant_sources"] is True
    assert "[Product:CR2032]" in captured_prompt
    assert "Lenovo mouse" not in captured_prompt
    assert "Touchpad issue" not in captured_prompt
    assert "Products and hardware recommendations" not in captured_prompt


@pytest.mark.anyio
async def test_execute_agent_query_returns_stages_and_grouped_evidence(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    memberships = [{"company_id": 1, "company_name": "Visible Co"}]
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
    monkeypatch.setattr(
        agent_service.rag_retrieval,
        "retrieve_candidates",
        AsyncMock(
            return_value=[
                {
                    "source_type": "tickets",
                    "source_id": 24425,
                    "title": "CMOS battery purchase",
                    "excerpt": "CMOS battery eBay listing expired.",
                    "score": 0.96,
                    "duplicate_count": 0,
                },
                {
                    "source_type": "chats",
                    "source_id": 30,
                    "title": "CMOS battery chat",
                    "excerpt": "Same CMOS battery question.",
                    "score": 0.82,
                    "duplicates": [
                        {
                            "source_type": "chats",
                            "source_id": 31,
                            "title": "Duplicate CMOS battery chat",
                        }
                    ],
                    "duplicate_count": 1,
                },
            ]
        ),
    )

    captured_prompt = ""
    llm_stages = []

    async def fake_trigger(slug, payload, *, background):
        nonlocal captured_prompt, llm_stages
        llm_stages.append(payload.get("stage"))
        captured_prompt = payload.get("prompt", "")
        return {"status": "succeeded", "response": {"response": "Curated answer"}}

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fake_trigger)

    result = await agent_service.execute_agent_query(
        "Trello CMOS battery", user, memberships=memberships
    )

    assert [stage["name"] for stage in result["stages"]] == [
        "query_understanding",
        "retrieval",
        "deduplication",
        "evidence_review",
        "category_summaries",
        "final_answer",
    ]
    assert result["stages"][2]["data"]["duplicates_grouped"] == 1
    assert result["evidence"]["tickets"][0]["label"] == "[Ticket:#24425]"
    assert result["evidence"]["chats"][0]["duplicate_count"] == 1
    assert "Also found in 1 similar results: [Chat:#31]" in captured_prompt
    assert llm_stages == [
        "query_understanding",
        "evidence_review",
        "category_summaries",
        "final_answer",
    ]


def test_filter_rag_candidates_does_not_duplicate_selected_sources(monkeypatch):
    monkeypatch.setattr(agent_service, "_threshold_for_source", lambda source_type: 0.0)

    result = agent_service._filter_rag_candidates(
        [
            {
                "source_type": "knowledge_base",
                "source_id": "reset-guide",
                "title": "Reset guide",
                "score": 0.9,
            }
        ],
        allowed_sources={"knowledge_base"},
    )

    assert len(result) == 1
    assert result[0]["source_id"] == "reset-guide"
    assert result[0]["was_selected_by_rag"] is True


@pytest.mark.anyio
async def test_execute_agent_query_passes_all_allowed_source_types_to_rag(monkeypatch):
    user = {"id": 7, "is_super_admin": False}
    memberships = [
        {
            "company_id": 1,
            "company_name": "Visible Co",
            "can_access_chat": True,
            "can_manage_assets": True,
            "can_manage_issues": True,
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
    monkeypatch.setattr(agent_service.db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agent_service, "_search_issue_sources", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        agent_service, "_search_feature_pack_sources", AsyncMock(return_value={})
    )
    retrieve_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        agent_service.rag_retrieval, "retrieve_candidates", retrieve_mock
    )
    monkeypatch.setattr(
        agent_service,
        "_invoke_agent_llm",
        AsyncMock(
            return_value={
                "status": "succeeded",
                "message": None,
                "text": "ok",
                "model": "test",
                "event_id": None,
            }
        ),
    )

    await agent_service.execute_agent_query(
        "network outage", user, memberships=memberships
    )

    source_filters = retrieve_mock.await_args.kwargs["source_filters"]
    assert source_filters == [
        "assets",
        "chats",
        "issues",
        "knowledge_base",
        "ticket_comments",
        "tickets",
    ]
