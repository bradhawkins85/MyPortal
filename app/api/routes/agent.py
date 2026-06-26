from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from app.services import agent as agent_service
from app.core.database import db
from app.repositories import rag_index as rag_index_repo
from app.repositories import rag_relationships as rag_relationship_repo

router = APIRouter(prefix="/api/agent", tags=["Agent"])
_RAG_INDEX_TASKS: dict[int, asyncio.Task[None]] = {}


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(
    payload: AgentQueryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> AgentQueryResponse:
    active_company_id = getattr(request.state, "active_company_id", None)
    memberships = getattr(request.state, "available_companies", None)
    result = await agent_service.execute_agent_query(
        payload.query,
        current_user,
        active_company_id=active_company_id,
        memberships=memberships,
    )
    return AgentQueryResponse(**result)


@router.post("/query/stream")
async def stream_agent_query(
    payload: AgentQueryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    active_company_id = getattr(request.state, "active_company_id", None)
    memberships = getattr(request.state, "available_companies", None)
    result = await agent_service.execute_agent_query(
        payload.query,
        current_user,
        active_company_id=active_company_id,
        memberships=memberships,
    )

    async def events():
        for stage in result.get("stages") or []:
            payload = {"event": "stage", **stage}
            yield f"data: {json.dumps(payload, default=str)}\n\n"
        evidence = (
            result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        )
        for source_type, items in evidence.items():
            if items:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "event": "evidence",
                            "source_type": source_type,
                            "count": len(items),
                        },
                        default=str,
                    )
                    + "\n\n"
                )
        if result.get("answer"):
            payload = {"event": "answer_delta", "text": result["answer"]}
            yield f"data: {json.dumps(payload, default=str)}\n\n"
        yield f"data: {json.dumps({'event': 'done'}, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/rag/health")
async def rag_health(current_user: dict = Depends(get_current_user)) -> dict:
    if not bool(current_user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    return await rag_index_repo.health()


async def _run_rag_index_job(
    job_id: int,
    current_user: dict,
    *,
    active_company_id: int | None = None,
    memberships: list[dict] | None = None,
) -> None:
    await rag_index_repo.update_job(
        job_id, status="running", message="Indexing started.", started=True
    )
    try:
        result = await agent_service.execute_agent_query(
            "",
            current_user,
            active_company_id=active_company_id,
            memberships=memberships,
            allow_empty_query=True,
            rag_index_job_id=job_id,
            cleanup_rag_index=True,
        )
        indexed = 0
        for value in result.get("sources", {}).values():
            if isinstance(value, list):
                indexed += len(value)
            elif isinstance(value, dict):
                indexed += sum(
                    len(rows or []) for rows in value.values() if isinstance(rows, list)
                )
        if await rag_index_repo.job_stop_requested(job_id):
            await rag_index_repo.update_job(
                job_id,
                status="cancelled",
                message="Indexing stopped by an administrator.",
                finished=True,
            )
            return
        await rag_index_repo.update_job(
            job_id,
            status="completed",
            message=f"Indexing completed. Refreshed up to {indexed} source records and cleaned stale RAG matchings.",
            finished=True,
        )
    except agent_service.rag_index_service.RagIndexCancelled:
        await rag_index_repo.update_job(
            job_id,
            status="cancelled",
            message="Indexing stopped by an administrator.",
            finished=True,
        )
    except Exception as exc:  # pragma: no cover - defensive background job guard
        if await rag_index_repo.job_stop_requested(job_id):
            await rag_index_repo.update_job(
                job_id,
                status="cancelled",
                message="Indexing stopped by an administrator.",
                finished=True,
            )
            return
        await rag_index_repo.update_job(
            job_id, status="failed", message=str(exc), finished=True
        )
    finally:
        _RAG_INDEX_TASKS.pop(job_id, None)


@router.post("/rag/index")
async def trigger_rag_index(
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    async with db.acquire_lock("rag_index_start", timeout=1) as lock_acquired:
        if not lock_acquired:
            active = await rag_index_repo.get_active_job()
            if active:
                return {
                    "job_id": int(active["id"]),
                    "status": active["status"],
                    "message": "An indexing job is already active.",
                }
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Unable to acquire indexing lock",
            )
        active = await rag_index_repo.get_active_job()
        if active:
            return {
                "job_id": int(active["id"]),
                "status": active["status"],
                "message": "An indexing job is already active.",
            }
        job_id = await rag_index_repo.create_job(source_type="all")
    active_company_id = getattr(request.state, "active_company_id", None)
    memberships = getattr(request.state, "available_companies", None)
    task = asyncio.create_task(
        _run_rag_index_job(
            job_id,
            dict(current_user),
            active_company_id=active_company_id,
            memberships=[dict(item) for item in memberships or []],
        ),
        name=f"rag-index-{job_id}",
    )
    _RAG_INDEX_TASKS[job_id] = task
    return {"job_id": job_id, "status": "queued"}


@router.delete("/rag/index/jobs/finished")
async def cleanup_finished_rag_index_jobs(
    current_user: dict = Depends(require_super_admin),
) -> dict:
    deleted = await rag_index_repo.cleanup_finished_jobs()
    return {"deleted": deleted}


@router.post("/rag/index/{job_id}/stop")
async def stop_rag_index(
    job_id: int,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    job = await rag_index_repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RAG index job not found"
        )
    job_status = str(job.get("status") or "")
    if job_status in {"completed", "failed", "cancelled"}:
        return {
            "job_id": job_id,
            "status": job_status,
            "message": "Job is already finished.",
        }
    await rag_index_repo.request_job_stop(job_id)
    task = _RAG_INDEX_TASKS.get(job_id)
    if task and not task.done():
        task.cancel()
        await rag_index_repo.update_job(
            job_id,
            status="cancelled",
            message="Indexing force-stopped by an administrator.",
            finished=True,
        )
        return {"job_id": job_id, "status": "cancelled"}
    return {"job_id": job_id, "status": "cancelling"}


@router.post("/rag/matching/pause")
async def pause_rag_matching(current_user: dict = Depends(require_super_admin)) -> dict:
    await rag_relationship_repo.set_matching_paused(True)
    return {"paused": True}


@router.post("/rag/matching/resume")
async def resume_rag_matching(
    current_user: dict = Depends(require_super_admin),
) -> dict:
    await rag_relationship_repo.set_matching_paused(False)
    return {"paused": False}


@router.delete("/rag/matching/stale")
async def cleanup_stale_rag_matches(
    current_user: dict = Depends(require_super_admin),
) -> dict:
    return await rag_relationship_repo.cleanup_stale_matches_and_decisions()
