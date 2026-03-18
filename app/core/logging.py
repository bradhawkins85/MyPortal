from __future__ import annotations

import json
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
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _sanitize_log_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_log_value(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _sanitize_log_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_log_value(value) for key, value in meta.items()}


def _format_meta(meta: dict[str, Any]) -> str:
    return " ".join(f"{key}={meta[key]}" for key in sorted(meta))


def log_error(
    message: str,
    *,
    exc: Exception | None = None,
    include_traceback: bool = False,
    **meta,
) -> None:
    if "exc_info" in meta:
        include_traceback = include_traceback or bool(meta.pop("exc_info"))
    sanitized_meta = _sanitize_log_meta(meta)
    if exc is not None:
        include_traceback = True
        sanitized_meta.setdefault("error_type", type(exc).__name__)
    if include_traceback:
        bound_logger = logger.bind(**sanitized_meta) if sanitized_meta else logger
        if sanitized_meta:
            bound_logger.exception(f"{message} | {_format_meta(sanitized_meta)}")
        else:
            bound_logger.exception(message)
        return
    if sanitized_meta:
        logger.bind(**sanitized_meta).error(f"{message} | {_format_meta(sanitized_meta)}")
    else:
        logger.error(message)


def log_info(message: str, **meta) -> None:
    meta = _sanitize_log_meta(meta)
    if meta:
        logger.bind(**meta).info(f"{message} | {_format_meta(meta)}")
    else:
        logger.info(message)


def log_warning(message: str, **meta) -> None:
    meta = _sanitize_log_meta(meta)
    if meta:
        logger.bind(**meta).warning(f"{message} | {_format_meta(meta)}")
    else:
        logger.warning(message)


def log_debug(message: str, **meta) -> None:
    meta = _sanitize_log_meta(meta)
    if meta:
        logger.bind(**meta).debug(f"{message} | {_format_meta(meta)}")
    else:
        logger.debug(message)


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
    meta.update(_sanitize_log_meta(extra_meta))

    message = " ".join(parts)
    if meta:
        message = f"{message} | {_format_meta(meta)}"
        logger.bind(**meta).info(message)
    else:
        logger.info(message)
