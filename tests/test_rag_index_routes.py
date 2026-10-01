import asyncio
from unittest.mock import AsyncMock

from app.api.routes import agent


def test_stop_rag_index_finishes_job_when_task_runs_on_another_worker(monkeypatch):
    monkeypatch.setattr(
        agent.rag_index_repo,
        "get_job",
        AsyncMock(return_value={"id": 42, "status": "running"}),
    )
    request_stop = AsyncMock(return_value=True)
    update_job = AsyncMock()
    monkeypatch.setattr(agent.rag_index_repo, "request_job_stop", request_stop)
    monkeypatch.setattr(agent.rag_index_repo, "update_job", update_job)
    agent._RAG_INDEX_TASKS.pop(42, None)

    result = asyncio.run(
        agent.stop_rag_index(42, current_user={"is_super_admin": True})
    )

    assert result == {"job_id": 42, "status": "cancelled"}
    request_stop.assert_awaited_once_with(42)
    update_job.assert_awaited_once_with(
        42,
        status="cancelled",
        message="Indexing stopped by an administrator.",
        finished=True,
    )


def test_rag_index_runner_records_task_cancellation(monkeypatch):
    update_job = AsyncMock()
    monkeypatch.setattr(agent.rag_index_repo, "update_job", update_job)
    monkeypatch.setattr(
        agent.agent_service,
        "execute_agent_query",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    try:
        asyncio.run(agent._run_rag_index_job(7, {"is_super_admin": True}))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("RAG index runner did not propagate task cancellation")

    assert update_job.await_args_list[-1].kwargs == {
        "status": "cancelled",
        "message": "Indexing task was cancelled.",
        "finished": True,
    }
