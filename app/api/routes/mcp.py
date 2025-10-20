from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.mcp import ChatGPTMCPError, handle_rpc_request

router = APIRouter(prefix="/api/mcp/chatgpt", tags=["ChatGPT MCP"])


@router.post("/", response_class=JSONResponse)
async def chatgpt_mcp_endpoint(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI already logs parse errors
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
    try:
        payload = await handle_rpc_request(body, authorization)
    except ChatGPTMCPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return JSONResponse(payload)
