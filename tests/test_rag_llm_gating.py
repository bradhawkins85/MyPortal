import pytest
from fastapi import HTTPException

from app.api.routes import agent as agent_routes
from app.services import component_availability, rag_relationships, scheduler
from app.services import modules as modules_service


@pytest.fixture
def rag_disabled(monkeypatch):
    monkeypatch.setattr(
        component_availability,
        "_availability",
        component_availability.ComponentAvailability(
            disabled_feature_packs=frozenset({"rag_index"})
        ),
    )


def test_rag_available_follows_feature_pack(rag_disabled):
    assert component_availability.rag_available() is False


def test_scheduler_identifies_rag_commands():
    assert scheduler._is_rag_command("rag_index_incremental")
    assert scheduler._is_rag_command("rag_cleanup_stale_matches")
    assert not scheduler._is_rag_command("sync_staff")
    assert not scheduler._is_rag_command(None)


@pytest.mark.anyio
async def test_relationship_batch_skips_module_check_when_rag_disabled(
    monkeypatch, rag_disabled
):
    async def fail(*args, **kwargs):
        raise AssertionError("RAG disabled: nothing should be queried")

    monkeypatch.setattr(rag_relationships.rel_repo, "matching_paused", fail)
    monkeypatch.setattr(rag_relationships.modules_service, "get_module", fail)

    assert await rag_relationships.evaluate_next_batch(limit=1) == 0
    assert (
        await rag_relationships.on_document_indexed(1, content_changed=True) == 0
    )


@pytest.mark.parametrize(
    "module, expected",
    [
        (None, False),
        ({"enabled": False, "settings": {"base_url": "http://llm"}}, False),
        ({"enabled": True, "settings": {"base_url": "  "}}, False),
        ({"enabled": True, "settings": {"base_url": "http://llm"}}, True),
    ],
)
def test_llm_module_ready(module, expected):
    assert modules_service.llm_module_ready(module) is expected


@pytest.mark.anyio
async def test_search_api_hidden_when_llm_unavailable(monkeypatch):
    async def unavailable():
        return False

    monkeypatch.setattr(agent_routes.modules_service, "llm_available", unavailable)
    with pytest.raises(HTTPException) as exc:
        await agent_routes.require_llm_search()
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_search_api_allowed_when_llm_available(monkeypatch):
    async def available():
        return True

    monkeypatch.setattr(agent_routes.modules_service, "llm_available", available)
    await agent_routes.require_llm_search()
