from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.mcp import ChatGPTMCPError
from app.services.mcp.chatgpt import handle_rpc_request as chatgpt_handle_rpc_request
from app.services.mcp.ollama import (
    OllamaMCPError,
    build_error_response,
    handle_rpc_request as ollama_handle_rpc_request,
    public_manifest as ollama_public_manifest,
)

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
        payload = await chatgpt_handle_rpc_request(body, authorization)
    except ChatGPTMCPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return JSONResponse(payload)


ollama_router = APIRouter(prefix="/api/mcp/ollama", tags=["Ollama MCP"])


async def _ollama_rpc(
    request: Request, authorization: str | None
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI already logs parse errors
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc
    request_id = None
    if isinstance(body, dict):
        request_id = body.get("id")
    try:
        payload = await ollama_handle_rpc_request(body, authorization)
    except OllamaMCPError as exc:
        # Wrap auth/disabled errors as HTTP errors so unauthenticated callers
        # get a proper status code; map other errors to JSON-RPC envelopes so
        # MCP clients can read them.
        if exc.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        }:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return JSONResponse(
            build_error_response(request_id, exc),
            status_code=status.HTTP_200_OK,
        )
    if payload is None:
        # JSON-RPC notifications produce no body; HTTP 204.
        return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(payload)


@ollama_router.post("/", response_class=JSONResponse)
async def ollama_mcp_endpoint(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    """JSON-RPC 2.0 / MCP endpoint consumed by Ollama MCP clients."""

    return await _ollama_rpc(request, authorization)


@ollama_router.post("/rpc", response_class=JSONResponse)
async def ollama_mcp_rpc_alias(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    """Alias for :func:`ollama_mcp_endpoint` for clients that expect ``/rpc``."""

    return await _ollama_rpc(request, authorization)


@ollama_router.get("/manifest", response_class=JSONResponse)
async def ollama_mcp_manifest() -> JSONResponse:
    """Return a non-secret discovery manifest for the Ollama MCP server."""

    return JSONResponse(ollama_public_manifest())


copilot_router = APIRouter(prefix="/api/mcp/copilot", tags=["Copilot MCP"])


@copilot_router.post("/", response_class=JSONResponse)
async def copilot_mcp_endpoint(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    """JSON-RPC 2.0 / MCP endpoint for GitHub Copilot and compatible clients.

    Delegates to the same Ollama-compatible handler as ``/api/mcp/ollama/``
    but uses a dedicated path so that Copilot traffic is clearly identified
    in logs and can be configured independently.
    """

    return await _ollama_rpc(request, authorization)


@copilot_router.post("/rpc", response_class=JSONResponse)
async def copilot_mcp_rpc_alias(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> JSONResponse:
    """Alias for :func:`copilot_mcp_endpoint` for clients that expect ``/rpc``."""

    return await _ollama_rpc(request, authorization)


@copilot_router.get("/manifest", response_class=JSONResponse)
async def copilot_mcp_manifest() -> JSONResponse:
    """Return the public discovery manifest for the Copilot MCP endpoint."""

    return JSONResponse(ollama_public_manifest())
