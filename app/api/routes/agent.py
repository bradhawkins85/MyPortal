from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.auth import get_current_user
from app.schemas.agent import AgentQueryRequest, AgentQueryResponse
from app.services import agent as agent_service

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
