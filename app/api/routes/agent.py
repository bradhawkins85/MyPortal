from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse, Response
import csv
import io

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.schemas.agent import (
    AgentQueryRequest,
    AgentQueryResponse,
    AgentSavedSearchCreateRequest,
    AgentSavedSearchItem,
    AgentFeedbackRequest,
)
from app.services import agent as agent_service
from app.core.database import db
from app.repositories import rag_index as rag_index_repo
from app.repositories import rag_relationships as rag_relationship_repo
from app.repositories import agent_saved_searches as saved_search_repo
from app.repositories import ai_quality as quality_repo
from app.services import audit as audit_service
from app.services import rag_outbox, rag_relationships as rag_relationship_service

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
    started = time.monotonic()
    result = await agent_service.execute_agent_query(
        payload.query,
        current_user,
        active_company_id=active_company_id,
        memberships=memberships,
        source_filters=payload.source_filters,
    )
    try:
        result["quality_response_id"] = await quality_repo.record_response(
            user_id=int(current_user["id"]), company_id=active_company_id,
            feature="agent", query=payload.query, evidence=result.get("evidence") or {},
            model=result.get("model"), latency_ms=int((time.monotonic() - started) * 1000),
            confidence_band=result.get("answer_confidence_label"),
            outcome="answered" if result.get("answer") else "no_answer",
        )
    except Exception:
        result["quality_response_id"] = None
    return AgentQueryResponse(**result)


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def submit_feedback(payload: AgentFeedbackRequest,
                          current_user: dict = Depends(get_current_user)) -> None:
    user_id = int(current_user["id"])
    if payload.reason and payload.reason not in quality_repo.REASONS:
        raise HTTPException(status_code=422, detail="Unknown feedback reason")
    if not await quality_repo.response_owned_by(payload.response_id, user_id):
        raise HTTPException(status_code=404, detail="AI response not found")
    await quality_repo.save_feedback(response_id=payload.response_id, user_id=user_id,
                                     rating=payload.rating, reason=payload.reason,
                                     comment=(payload.comment or "").strip())


@router.get("/quality/summary")
async def quality_summary(_: dict = Depends(require_super_admin)) -> dict:
    """Return only de-identified aggregates; source records are never exposed."""
    return {"groups": await quality_repo.aggregate(), "pipeline_version": quality_repo.PIPELINE_VERSION}


@router.get("/quality/export.csv", response_class=Response)
async def quality_export(_: dict = Depends(require_super_admin)) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["feature", "source_type", "provider", "confidence_band", "response_count", "helpful", "unhelpful", "average_latency_ms"])
    writer.writeheader()
    writer.writerows(await quality_repo.aggregate())
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ai-quality-summary.csv"})


@router.post("/query/stream")
async def stream_agent_query(
    payload: AgentQueryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    active_company_id = getattr(request.state, "active_company_id", None)
    memberships = getattr(request.state, "available_companies", None)
    async def events():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def publish(event: dict) -> None:
            await queue.put(event)

        async def run_query() -> None:
            try:
                result = await agent_service.execute_agent_query(
                    payload.query,
                    current_user,
                    active_company_id=active_company_id,
                    memberships=memberships,
                    source_filters=payload.source_filters,
                    event_callback=publish,
                )
                try:
                    result["quality_response_id"] = await quality_repo.record_response(
                        user_id=int(current_user["id"]),
                        company_id=active_company_id,
                        feature="agent",
                        query=payload.query,
                        evidence=result.get("evidence") or {},
                        model=result.get("model"),
                        latency_ms=int((result.get("metrics") or {}).get("total_latency_ms") or 0),
                        confidence_band=result.get("answer_confidence_label"),
                        outcome="answered" if result.get("answer") else "no_answer",
                    )
                except Exception:
                    result["quality_response_id"] = None
                await queue.put({"event": "result", **result})
                await queue.put({"event": "done", "metrics": result.get("metrics")})
            except asyncio.CancelledError:
                raise
            except Exception:
                await queue.put(
                    {"event": "error", "message": "Unable to process the request."}
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_query())
        try:
            # Flush headers and visible progress before retrieval or generation ends.
            yield f"data: {json.dumps({'event': 'started'})}\n\n"
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/saved-searches", response_model=list[AgentSavedSearchItem])
async def list_saved_searches(
    current_user: dict = Depends(get_current_user),
) -> list[AgentSavedSearchItem]:
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid"
        )
    records = await saved_search_repo.list_for_user(user_id)
    return [AgentSavedSearchItem(**item) for item in records]


@router.post("/saved-searches", response_model=AgentSavedSearchItem)
async def create_saved_search(
    payload: AgentSavedSearchCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> AgentSavedSearchItem:
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid"
        )
    is_super_admin = bool(current_user.get("is_super_admin"))
    item = await saved_search_repo.create_or_update(
        user_id=user_id,
        name=payload.name,
        query_text=payload.query,
        source_filters=list(payload.source_filters),
        is_shared=bool(payload.is_shared) and is_super_admin,
    )
    return AgentSavedSearchItem(**item)


@router.delete("/saved-searches/{saved_search_id}")
async def delete_saved_search(
    saved_search_id: int,
    current_user: dict = Depends(get_current_user),
) -> dict[str, bool]:
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid"
        )
    deleted = await saved_search_repo.delete_for_user(
        saved_search_id=saved_search_id,
        user_id=user_id,
        allow_shared=bool(current_user.get("is_super_admin")),
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found"
        )
    return {"deleted": True}


@router.get("/rag/health")
async def rag_health(current_user: dict = Depends(get_current_user)) -> dict:
    if not bool(current_user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    return await rag_index_repo.health()


def _safe_error_category(value: object) -> str | None:
    text = str(value or "").casefold()
    if not text:
        return None
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate" in text or "429" in text:
        return "rate_limited"
    if "unavailable" in text or "connect" in text:
        return "provider_unavailable"
    if "parse" in text or "json" in text or "format" in text:
        return "invalid_model_response"
    return "evaluation_failed"


def _is_stale(document: dict) -> bool:
    updated = document.get("source_updated_at")
    indexed = document.get("indexed_at")
    if not updated or not indexed:
        return False
    if isinstance(updated, datetime) and isinstance(indexed, datetime):
        return updated > indexed
    return str(updated) > str(indexed)


@router.get("/rag/diagnostics/tickets/{ticket_id}")
async def ticket_rag_diagnostic(
    ticket_id: int,
    _: dict = Depends(require_super_admin),
) -> dict:
    """Inspect one ticket without returning indexed ticket or candidate content."""
    document = await rag_index_repo.get_document_diagnostic("tickets", str(ticket_id))
    if not document:
        return {
            "ticket_id": ticket_id, "indexed": False, "current": False,
            "eligible": False, "ineligibility_reason": "No indexed document exists.",
            "document": None, "candidates": [], "queue": [], "relationships": [],
        }
    document_id = int(document["id"])
    decisions = await rag_relationship_repo.document_decisions(document_id)
    settings = agent_service.rag_index_service.get_settings()
    scope = rag_relationship_repo._json(document.get("permission_scope_json"), {})
    public_document = {
        key: document.get(key) for key in (
            "id", "source_type", "source_id", "company_id", "title", "embedding_model",
            "is_active", "source_updated_at", "indexed_at",
        )
    }
    public_document["permission_scope"] = scope
    public_document["chunks"] = document.get("chunks") or []
    queue = []
    for row in decisions["queue"]:
        row = dict(row)
        row["error_category"] = _safe_error_category(row.pop("last_error", None))
        row["model"] = settings.rag_relationship_model
        row["next_action"] = "Retry failed evaluation" if row["status"] == "FAILED" else "Wait for evaluator"
        queue.append(row)
    relationships = []
    for row in decisions["relationships"]:
        row = dict(row)
        row["stale"] = (
            row.get("source_hash") != row.get("current_source_hash")
            or row.get("target_hash") != row.get("current_target_hash")
            or row.get("match_status") == "STALE"
        )
        for key in ("source_hash", "target_hash", "current_source_hash", "current_target_hash"):
            row.pop(key, None)
        relationships.append(row)
    active = bool(document.get("is_active"))
    has_active_chunks = any(bool(chunk.get("is_active")) for chunk in document.get("chunks") or [])
    current = active and not _is_stale(document)
    return {
        "ticket_id": ticket_id, "indexed": True, "current": current,
        "eligible": active and has_active_chunks,
        "ineligibility_reason": None if active and has_active_chunks else "Document is inactive or has no active chunks.",
        "document": public_document,
        "configuration": {
            "candidate_limit": settings.rag_relationship_candidate_limit,
            "ticket_candidate_limit": settings.rag_relationship_ticket_candidate_limit,
            "relationship_min_score": settings.rag_relationship_min_score,
            "relationship_model": settings.rag_relationship_model,
        },
        "candidates": await rag_relationship_repo.candidate_diagnostics(document_id),
        "queue": queue, "relationships": relationships,
    }


@router.post("/rag/diagnostics/tickets/{ticket_id}/reindex")
async def reindex_ticket(ticket_id: int, _: dict = Depends(require_super_admin)) -> dict:
    """Re-index one ticket and return final counts."""
    job_id = await rag_index_repo.create_job("tickets", str(ticket_id))
    await rag_index_repo.update_job(job_id, status="running", message="Refreshing ticket index.", started=True)
    try:
        counts = await rag_outbox.reindex_source("tickets", str(ticket_id))
        await rag_index_repo.update_job(job_id, status="completed", message=f"Refresh completed: {counts['indexed']} indexed, {counts['deactivated']} deactivated.", finished=True)
        await audit_service.log_action(action="rag.ticket.reindex", user_id=int(_["id"]), entity_type="ticket", entity_id=ticket_id, metadata=counts)
        return {"job_id": job_id, "status": "completed", **counts}
    except Exception:
        await rag_index_repo.update_job(job_id, status="failed", message="Ticket indexing failed. Review server logs.", finished=True)
        raise HTTPException(status_code=503, detail="Ticket indexing failed")


@router.post("/rag/diagnostics/tickets/{ticket_id}/relationships/rebuild")
async def rebuild_ticket_relationships(ticket_id: int, _: dict = Depends(require_super_admin)) -> dict:
    document = await rag_index_repo.get_document_diagnostic("tickets", str(ticket_id))
    if not document or not document.get("is_active"):
        raise HTTPException(status_code=404, detail="Indexed ticket not found")
    await rag_relationship_repo.mark_relationships_stale(int(document["id"]))
    queued = await rag_relationship_service.enqueue_relationships_for_document(int(document["id"]))
    await audit_service.log_action(action="rag.ticket.relationships.rebuild", user_id=int(_["id"]), entity_type="ticket", entity_id=ticket_id, metadata={"queued": queued})
    return {"status": "completed", "relationships_staled": True, "queued": queued}


@router.post("/rag/diagnostics/tickets/{ticket_id}/relationships/retry")
async def retry_ticket_relationships(ticket_id: int, _: dict = Depends(require_super_admin)) -> dict:
    document = await rag_index_repo.get_document_diagnostic("tickets", str(ticket_id))
    if not document:
        raise HTTPException(status_code=404, detail="Indexed ticket not found")
    retried = await rag_relationship_repo.retry_failed_for_document(int(document["id"]))
    await audit_service.log_action(action="rag.ticket.relationships.retry", user_id=int(_["id"]), entity_type="ticket", entity_id=ticket_id, metadata={"retried": retried})
    return {"status": "completed", "retried": retried}


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
    except asyncio.CancelledError:
        await rag_index_repo.update_job(
            job_id,
            status="cancelled",
            message="Indexing task was cancelled.",
            finished=True,
        )
        raise
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
        await rag_index_repo.update_job(
            job_id,
            status="cancelled",
            message="Indexing force-stopped by an administrator.",
            finished=True,
        )
        task.cancel()
        return {"job_id": job_id, "status": "cancelled"}
    # Background tasks are process-local, so an API request handled by another
    # worker cannot access the Task object.  Mark the persisted job terminal;
    # the indexing loop observes cancelled as a stop request on its next check.
    await rag_index_repo.update_job(
        job_id,
        status="cancelled",
        message="Indexing stopped by an administrator.",
        finished=True,
    )
    return {"job_id": job_id, "status": "cancelled"}


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
