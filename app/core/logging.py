from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger


# Context variables that carry per-request state into every log line emitted
# while handling that request. Set by the request logging middleware (and any
# other entry point such as scheduled tasks).
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id_ctx: ContextVar[int | None] = ContextVar("user_id", default=None)
_route_ctx: ContextVar[str | None] = ContextVar("route", default=None)
_client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_request_context(
    *,
    request_id: str | None = None,
    user_id: int | None = None,
    route: str | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """Set per-request context vars and return reset tokens for later restore.

    The returned dict can be passed to :func:`reset_request_context` to undo
    only the keys that were actually set. This makes the helper safe to call
    multiple times during a request without leaking state across requests.
    """

    tokens: dict[str, Any] = {}
    if request_id is not None:
        tokens["request_id"] = _request_id_ctx.set(request_id)
    if user_id is not None:
        tokens["user_id"] = _user_id_ctx.set(user_id)
    if route is not None:
        tokens["route"] = _route_ctx.set(route)
    if client_ip is not None:
        tokens["client_ip"] = _client_ip_ctx.set(client_ip)
    return tokens


def reset_request_context(tokens: dict[str, Any]) -> None:
    """Reset context vars previously set via :func:`set_request_context`."""

    for key, token in tokens.items():
        try:
            if key == "request_id":
                _request_id_ctx.reset(token)
            elif key == "user_id":
                _user_id_ctx.reset(token)
            elif key == "route":
                _route_ctx.reset(token)
            elif key == "client_ip":
                _client_ip_ctx.reset(token)
        except (LookupError, ValueError):  # pragma: no cover - defensive
            continue


def get_request_context() -> dict[str, Any]:
    """Return a snapshot of the current request context for logs/audit."""

    snapshot: dict[str, Any] = {}
    request_id = _request_id_ctx.get()
    if request_id:
        snapshot["request_id"] = request_id
    user_id = _user_id_ctx.get()
    if user_id is not None:
        snapshot["user_id"] = user_id
    route = _route_ctx.get()
    if route:
        snapshot["route"] = route
    client_ip = _client_ip_ctx.get()
    if client_ip:
        snapshot["client_ip"] = client_ip
    return snapshot


def configure_logging() -> None:
    from app.core.config import get_settings

    logger.remove()
    log_format = (
        "{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | "
        "{extra[request_id]} | {extra[user_id]} | {message}\n{exception}"
    )

    # Default extras keep the format string safe even when context is missing.
    logger.configure(extra={"request_id": "-", "user_id": "-"})

    def _format_record(record: dict[str, Any]) -> str:  # pragma: no cover - thin shim
        record.setdefault("extra", {})
        record["extra"].setdefault("request_id", "-")
        record["extra"].setdefault("user_id", "-")
        return log_format

    logger.add(sink=lambda msg: print(msg, end=""), format=_format_record)

    settings = get_settings()
    log_path = settings.fail2ban_log_path
    if log_path:
        log_path = log_path.expanduser()
        if _ensure_log_path(log_path):
            try:
                rotation = _coerce_optional(getattr(settings, "log_rotation", None))
                retention = _coerce_optional(getattr(settings, "log_retention", None))
                compression = _coerce_optional(getattr(settings, "log_compression", None))
                logger.add(
                    str(log_path),
                    format=_format_record,
                    level="INFO",
                    encoding="utf-8",
                    enqueue=True,
                    rotation=rotation,
                    retention=retention,
                    compression=compression,
                )
            except Exception as exc:  # pragma: no cover - defensive logging setup
                logger.warning(
                    f"AUTH LOG FILE DISABLED - unable to open file path={log_path} error={exc}"
                )

    error_log_path = getattr(settings, "error_log_path", None)
    if error_log_path:
        error_log_path = error_log_path.expanduser()
        if _ensure_log_path(error_log_path):
            try:
                rotation = _coerce_optional(getattr(settings, "log_rotation", None))
                retention = _coerce_optional(getattr(settings, "log_retention", None))
                compression = _coerce_optional(getattr(settings, "log_compression", None))
                logger.add(
                    str(error_log_path),
                    format=_format_record,
                    level="WARNING",
                    encoding="utf-8",
                    enqueue=True,
                    rotation=rotation,
                    retention=retention,
                    compression=compression,
                )
            except Exception as exc:  # pragma: no cover - defensive logging setup
                logger.warning(
                    f"ERROR LOG FILE DISABLED - unable to open file path={error_log_path} error={exc}"
                )


def _coerce_optional(value: Any) -> Any:
    """Treat empty strings as 'not configured' for loguru's add() kwargs."""

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _sanitize_log_value(value: Any) -> Any:
    if isinstance(value, datetime):
        target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return target.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _sanitize_log_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_log_value(item) for item in value]
    if isinstance(value, Exception):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _sanitize_log_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {key: _sanitize_log_value(value) for key, value in meta.items()}


def _format_meta(meta: dict[str, Any]) -> str:
    return " ".join(f"{key}={meta[key]}" for key in sorted(meta))


def _merge_with_context(meta: dict[str, Any]) -> dict[str, Any]:
    """Merge explicit metadata with the active request context.

    Explicit metadata always wins over context values so that callers can
    override (for example, log on behalf of a different user).
    """

    merged = dict(get_request_context())
    merged.update(meta)
    return merged


def _log_with_meta(level: str, message: str, **meta: Any) -> None:
    enriched_meta = _merge_with_context(meta)
    sanitized_meta = _sanitize_log_meta(enriched_meta)
    if sanitized_meta:
        logger.bind(**sanitized_meta).log(level, f"{message} | {_format_meta(sanitized_meta)}")
    else:
        logger.log(level, message)


def log_error(
    message: str,
    *,
    exc: Exception | None = None,
    include_traceback: bool = False,
    **meta: Any,
) -> None:
    exc_info = bool(meta.pop("exc_info", False))
    include_exception = exc is not None
    include_traceback = include_traceback or exc_info or include_exception
    enriched_meta = _merge_with_context(meta)
    sanitized_meta = _sanitize_log_meta(enriched_meta)

    if exc is not None and "error_type" not in sanitized_meta:
        sanitized_meta["error_type"] = type(exc).__name__

    rendered_message = message
    if sanitized_meta:
        rendered_message = f"{message} | {_format_meta(sanitized_meta)}"

    logger_instance = logger.bind(**sanitized_meta) if sanitized_meta else logger
    if exc is not None:
        logger_instance.opt(exception=exc).error(rendered_message)
        return
    if include_traceback:
        logger_instance.exception(rendered_message)
        return
    logger_instance.error(rendered_message)


def log_info(message: str, **meta: Any) -> None:
    _log_with_meta("INFO", message, **meta)


def log_warning(message: str, **meta: Any) -> None:
    _log_with_meta("WARNING", message, **meta)


def log_debug(message: str, **meta: Any) -> None:
    _log_with_meta("DEBUG", message, **meta)


def _ensure_log_path(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            f"AUTH LOG FILE DISABLED - unable to create directory path={path.parent} "
            f"error={exc}"
        )
        return False
    return True


def log_audit_event(
    event_type: str,
    action: str,
    *,
    user_id: int | None = None,
    user_email: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    ip_address: str | None = None,
    **extra_meta,
) -> None:
    """
    Log an audit event to disk in a consistent format for external tools.

    This function writes audit events to the disk-based log file configured
    via FAIL2BAN_LOG_PATH. Events are logged in a structured format compatible
    with tools like Fail2ban or SIEM platforms.

    Format: ``{event_type} {action} | user_id={...} entity_type={...} entity_id={...} ip={...} [extra_meta]``

    Args:
        event_type: Category of the event (e.g., "API OPERATION", "BCP ACTION")
        action: Specific action performed (e.g., "create", "update", "delete")
        user_id: ID of the user performing the action
        user_email: Email of the user performing the action
        entity_type: Type of entity being acted upon (e.g., "risk", "objective")
        entity_id: ID of the entity being acted upon
        ip_address: IP address of the client
        **extra_meta: Additional metadata to include in the log entry
    """
    parts = [event_type, action]
    meta: dict[str, Any] = {}

    if user_id is not None:
        meta["user_id"] = user_id
    if user_email:
        meta["user_email"] = user_email
    if entity_type:
        meta["entity_type"] = entity_type
    if entity_id is not None:
        meta["entity_id"] = entity_id
    if ip_address:
        meta["ip"] = ip_address

    # Add any extra metadata
    meta.update(extra_meta)
    # Auto-merge contextual fields (request_id, route, ...) so that disk audit
    # log lines can be correlated back to server log entries.
    meta = _merge_with_context(meta)
    sanitized_meta = _sanitize_log_meta(meta)

    message = " ".join(parts)
    if sanitized_meta:
        message = f"{message} | {_format_meta(sanitized_meta)}"
        logger.bind(**sanitized_meta).info(message)
    else:
        logger.info(message)
