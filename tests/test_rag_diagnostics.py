from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from app.api.routes import agent
from app.repositories import rag_relationships


def test_candidate_score_components_explain_final_score() -> None:
    components = rag_relationships._candidate_score_components(
        {"metadata_json": '{"ticket_id": "ABC-42"}'},
        "printer ABC-42 offline",
        [[1.0, 0.0]],
        {
            "title": "ABC-42 printer",
            "candidate_text": "printer repair",
            "candidate_vectors": [[1.0, 0.0]],
            "metadata_json": "{}",
        },
    )

    assert components["semantic_score"] == 1.0
    assert components["metadata_score"] == 1.0
    assert components["source_weight"] == 1.0
    assert components["final_score"] == pytest.approx(
        components["semantic_score"] * 0.5
        + components["lexical_score"] * 0.3
        + components["metadata_score"] * 0.2
    )


def test_ticket_diagnostic_redacts_content_and_safe_errors(monkeypatch) -> None:
    document = {
        "id": 8, "source_type": "tickets", "source_id": "42", "company_id": 3,
        "title": "Printer issue", "embedding_model": "embed-v2", "is_active": 1,
        "indexed_at": "2026-01-02", "source_updated_at": "2026-01-01",
        "permission_scope_json": '{"version":1,"visibility":"company","company_ids":[3]}',
        "chunks": [{"chunk_index": 0, "chunk_hash": "safe-hash", "token_count": 12,
                    "character_count": 80, "embedding_model": "embed-v2", "is_active": 1}],
    }
    monkeypatch.setattr(agent.rag_index_repo, "get_document_diagnostic", AsyncMock(return_value=document))
    monkeypatch.setattr(agent.rag_relationship_repo, "candidate_diagnostics", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.rag_relationship_repo, "document_decisions", AsyncMock(return_value={
        "queue": [{"status": "FAILED", "last_error": "secret token abc: provider timeout", "retry_count": 5}],
        "relationships": [],
    }))
    monkeypatch.setattr(agent.agent_service.rag_index_service, "get_settings", lambda: SimpleNamespace(
        rag_relationship_candidate_limit=24, rag_relationship_ticket_candidate_limit=5,
        rag_relationship_min_score=0.55, rag_relationship_model="safe-model",
    ))

    result = asyncio.run(agent.ticket_rag_diagnostic(42, {}))

    assert result["indexed"] is True
    assert result["eligible"] is True
    assert result["queue"][0]["error_category"] == "timeout"
    assert "last_error" not in result["queue"][0]
    assert "secret token" not in str(result)
    assert "chunk_text" not in str(result)


def test_missing_ticket_diagnostic_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(agent.rag_index_repo, "get_document_diagnostic", AsyncMock(return_value=None))
    result = asyncio.run(agent.ticket_rag_diagnostic(404, {}))
    assert result["indexed"] is False
    assert result["eligible"] is False
    assert result["document"] is None


def test_diagnostic_endpoints_require_super_admin() -> None:
    routes = {
        route.path: route
        for route in agent.router.routes
        if "/rag/diagnostics/tickets/" in route.path
    }
    assert len(routes) == 4
    for route in routes.values():
        dependencies = [dependency.call for dependency in route.dependant.dependencies]
        assert agent.require_super_admin in dependencies
