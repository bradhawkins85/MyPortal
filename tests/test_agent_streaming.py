import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import agent as agent_routes
from app.schemas.agent import AgentQueryRequest


@pytest.mark.anyio
async def test_stream_sends_started_before_query_completes(monkeypatch):
    release = asyncio.Event()

    async def execute(*_args, event_callback, **_kwargs):
        await event_callback({"event": "stage", "name": "retrieval", "status": "complete"})
        await release.wait()
        return {"answer": "done", "evidence": {}, "metrics": {"model_calls": 1}}

    monkeypatch.setattr(agent_routes.agent_service, "execute_agent_query", execute)
    monkeypatch.setattr(agent_routes.quality_repo, "record_response", AsyncMock(return_value=3))
    request = SimpleNamespace(
        state=SimpleNamespace(active_company_id=None, available_companies=[]),
        is_disconnected=AsyncMock(return_value=False),
    )
    response = await agent_routes.stream_agent_query(
        AgentQueryRequest(query="status", source_filters=["tickets"]),
        request,
        {"id": 1},
    )
    stream = response.body_iterator

    first = await stream.__anext__()
    assert '"event": "started"' in first
    release.set()
    remaining = [chunk async for chunk in stream]
    assert any('"event": "done"' in chunk for chunk in remaining)


@pytest.mark.anyio
async def test_stream_close_cancels_provider_work(monkeypatch):
    cancelled = asyncio.Event()

    async def execute(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(agent_routes.agent_service, "execute_agent_query", execute)
    request = SimpleNamespace(
        state=SimpleNamespace(active_company_id=None, available_companies=[]),
        is_disconnected=AsyncMock(return_value=False),
    )
    response = await agent_routes.stream_agent_query(
        AgentQueryRequest(query="status", source_filters=["tickets"]),
        request,
        {"id": 1},
    )
    stream = response.body_iterator
    await stream.__anext__()
    await asyncio.sleep(0)
    await stream.aclose()
    assert cancelled.is_set()
