from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from app.services import agent as agent_service
from app.repositories import rag_index as rag_index_repo

router = APIRouter(prefix="/api/agent", tags=["Agent"])


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
        )
        indexed = 0
        for value in result.get("sources", {}).values():
            if isinstance(value, list):
                indexed += len(value)
            elif isinstance(value, dict):
                indexed += sum(
                    len(rows or []) for rows in value.values() if isinstance(rows, list)
                )
        await rag_index_repo.update_job(
            job_id,
            status="completed",
            message=f"Indexing completed. Refreshed up to {indexed} source records.",
            finished=True,
        )
    except Exception as exc:  # pragma: no cover - defensive background job guard
        await rag_index_repo.update_job(
            job_id, status="failed", message=str(exc), finished=True
        )


@router.post("/rag/index")
async def trigger_rag_index(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    job_id = await rag_index_repo.create_job(source_type="all")
    active_company_id = getattr(request.state, "active_company_id", None)
    memberships = getattr(request.state, "available_companies", None)
    background_tasks.add_task(
        _run_rag_index_job,
        job_id,
        dict(current_user),
        active_company_id=active_company_id,
        memberships=[dict(item) for item in memberships or []],
    )
    return {"job_id": job_id, "status": "queued"}
