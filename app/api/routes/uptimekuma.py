from __future__ import annotations

import json
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import ValidationError

from app.api.dependencies.auth import require_super_admin
from app.schemas.uptimekuma import (
    UptimeKumaAlertIngestResponse,
    UptimeKumaAlertPayload,
    UptimeKumaAlertResponse,
)
from app.services import uptimekuma as uptimekuma_service
from app.services import webhook_monitor

router = APIRouter(prefix="/api/integration-modules/uptimekuma", tags=["Uptime Kuma"])


@router.post(
    "/alerts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UptimeKumaAlertIngestResponse,
    name="uptimekuma_receive_alert",
)
async def receive_alert(
    request: Request,
    token: str | None = Query(default=None, description="Optional token fallback when Authorization header is unavailable."),
) -> UptimeKumaAlertIngestResponse:
    def _redact_source_url(url: str) -> str:
        parts = urlsplit(url)
        if not parts.query:
            return url

        sensitive_query_keys = {"token", "secret", "shared_secret", "sharedSecret"}
        redacted_pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key in sensitive_query_keys:
                redacted_pairs.append((key, "***REDACTED***"))
            else:
                redacted_pairs.append((key, value))

        redacted_query = urlencode(redacted_pairs, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))

    def _extract_secret_from_payload(payload_data: dict[str, object]) -> str | None:
        for key in ("token", "shared_secret", "sharedSecret", "secret"):
            value = payload_data.get(key)
            if value is None:
                continue
            token_value = str(value).strip()
            if token_value:
                return token_value
        return None

    request_headers = dict(request.headers)
    source_url = _redact_source_url(str(request.url))
    content_type = (request.headers.get("content-type") or "").lower()
    raw_payload: dict[str, object]

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        raw_payload = {key: value for key, value in form_data.items()}
        decoded_body = "&".join(f"{key}={value}" for key, value in raw_payload.items())
    else:
        raw_body = await request.body()
        decoded_body = raw_body.decode("utf-8", errors="replace") if raw_body else ""
    if decoded_body and "application/json" in content_type:
        try:
            candidate_payload = json.loads(decoded_body)
            raw_payload = candidate_payload if isinstance(candidate_payload, dict) else {"_raw": candidate_payload}
        except json.JSONDecodeError as exc:
            await webhook_monitor.log_incoming_webhook(
                name="Uptime Kuma Webhook - Invalid JSON",
                source_url=source_url,
                payload=decoded_body,
                headers=request_headers,
                response_status=status.HTTP_400_BAD_REQUEST,
                response_body="Invalid JSON payload",
                error_message=f"Invalid JSON payload: {exc}",
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
    elif decoded_body:
        form_values = parse_qs(decoded_body, keep_blank_values=True)
        raw_payload = {key: values[-1] if values else "" for key, values in form_values.items()}
    else:
        raw_payload = {}

    try:
        payload = UptimeKumaAlertPayload.model_validate(raw_payload)
    except ValidationError as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Uptime Kuma Webhook - Validation Failed",
            source_url=source_url,
            payload=raw_payload or decoded_body,
            headers=request_headers,
            response_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            response_body="Validation failed",
            error_message="Invalid Uptime Kuma payload schema",
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid Uptime Kuma payload schema") from exc

    logger.debug(
        "Uptime Kuma webhook request received",
        method=request.method,
        url_path=request.url.path,
        has_query_params=bool(request.url.query),
        content_type=content_type,
        user_agent=request.headers.get("user-agent"),
        remote_addr=request.client.host if request.client else None,
        has_auth_header=bool(request.headers.get("authorization")),
        has_token_param=bool(token),
        payload_status=payload.status,
        payload_alert_type=payload.alert_type,
        payload_title=payload.title,
        payload_monitor_name=payload.monitor_name,
    )
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    provided_secret: str | None = None
    if auth_header and auth_header.lower().startswith("bearer "):
        provided_secret = auth_header.split(" ", 1)[1].strip()
    if not provided_secret and token:
        provided_secret = token.strip() or None
    if not provided_secret:
        provided_secret = _extract_secret_from_payload(raw_payload)

    remote_addr = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent") or request.headers.get("User-Agent")
    normalised_payload = payload.model_dump(mode="json", by_alias=True)

    try:
        record = await uptimekuma_service.ingest_alert(
            payload=payload,
            raw_payload=normalised_payload,
            provided_secret=provided_secret,
            remote_addr=remote_addr,
            user_agent=user_agent,
        )
    except uptimekuma_service.AuthenticationError as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Uptime Kuma Webhook - Authentication Failed",
            source_url=source_url,
            payload=normalised_payload,
            headers=request_headers,
            response_status=status.HTTP_401_UNAUTHORIZED,
            response_body=str(exc),
            error_message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except uptimekuma_service.ModuleDisabledError as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Uptime Kuma Webhook - Module Disabled",
            source_url=source_url,
            payload=normalised_payload,
            headers=request_headers,
            response_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            response_body=str(exc),
            error_message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Uptime Kuma Webhook - Invalid Payload",
            source_url=source_url,
            payload=normalised_payload,
            headers=request_headers,
            response_status=status.HTTP_400_BAD_REQUEST,
            response_body=str(exc),
            error_message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        await webhook_monitor.log_incoming_webhook(
            name="Uptime Kuma Webhook - Error",
            source_url=source_url,
            payload=normalised_payload,
            headers=request_headers,
            response_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            response_body=str(exc),
            error_message=str(exc),
        )
        raise

    monitor_name = record.get("monitor_name") or "Unknown Monitor"
    await webhook_monitor.log_incoming_webhook(
        name=f"Uptime Kuma Webhook - {monitor_name}",
        source_url=source_url,
        payload=normalised_payload,
        headers=request_headers,
        response_status=status.HTTP_202_ACCEPTED,
        response_body="accepted",
    )

    return UptimeKumaAlertIngestResponse(
        status="accepted",
        alert_id=record["id"],
        service_status_updated=bool(record.get("service_status_updated", False)),
    )


@router.get("/alerts", response_model=list[UptimeKumaAlertResponse])
async def list_alerts(
    status_filter: str | None = Query(default=None, alias="status"),
    monitor_id: int | None = Query(default=None),
    important: bool | None = Query(default=None, description="Filter alerts marked as important."),
    search: str | None = Query(default=None, description="Case-insensitive search across message, reason, and monitor name."),
    sort_by: str = Query(default="received_at"),
    sort_direction: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_super_admin),
) -> list[UptimeKumaAlertResponse]:
    records = await uptimekuma_service.list_alerts(
        status=status_filter,
        monitor_id=monitor_id,
        importance=important,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    return [UptimeKumaAlertResponse(**record) for record in records]


@router.get("/alerts/{alert_id}", response_model=UptimeKumaAlertResponse)
async def get_alert(alert_id: int, current_user: dict = Depends(require_super_admin)) -> UptimeKumaAlertResponse:
    record = await uptimekuma_service.get_alert(alert_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return UptimeKumaAlertResponse(**record)
