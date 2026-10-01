"""Provider-facing Voice Monitor webhooks (not a web health surface)."""
import os
from fastapi import APIRouter, Header, HTTPException, Request

from app.services.voice_monitor.callbacks import InvalidCallback, handle_callback

router = APIRouter(prefix="/api/voice-monitor/provider", tags=["Voice Monitor Provider"])


@router.post("/callback")
async def provider_callback(request: Request, x_provider_signature: str | None = Header(None),
                            x_provider_timestamp: str | None = Header(None)):
    try:
        accepted = await handle_callback(await request.body(), x_provider_signature,
                                         x_provider_timestamp, os.getenv("VOICE_MONITOR_CALLBACK_SECRET", ""))
    except InvalidCallback as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"accepted": accepted}
