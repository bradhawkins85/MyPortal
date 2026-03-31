from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger


def configure_logging() -> None:
    from app.core.config import get_settings

    logger.remove()
    log_format = "{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {message}\n{exception}"
    logger.add(sink=lambda msg: print(msg, end=""), format=log_format)

    settings = get_settings()
    log_path = settings.fail2ban_log_path
    if log_path:
        log_path = log_path.expanduser()
        if _ensure_log_path(log_path):
            try:
                logger.add(
                    str(log_path),
                    format=log_format,
                    level="INFO",
                    encoding="utf-8",
                    enqueue=True,
                )
            except Exception as exc:  # pragma: no cover - defensive logging setup
                logger.warning(
                    f"AUTH LOG FILE DISABLED - unable to open file path={log_path} error={exc}"
                )


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


def _log_with_meta(level: str, message: str, **meta: Any) -> None:
    sanitized_meta = _sanitize_log_meta(meta)
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
    sanitized_meta = _sanitize_log_meta(meta)

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
    sanitized_meta = _sanitize_log_meta(meta)

    message = " ".join(parts)
    if sanitized_meta:
        message = f"{message} | {_format_meta(sanitized_meta)}"
        logger.bind(**sanitized_meta).info(message)
    else:
        logger.info(message)
