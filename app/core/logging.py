from __future__ import annotations

from pathlib import Path

from loguru import logger


def configure_logging() -> None:
    from app.core.config import get_settings

    logger.remove()
    log_format = "{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {message}"
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


def log_info(message: str, **meta) -> None:
    if meta:
        logger.bind(**meta).info(message)
    else:
        logger.info(message)


def log_error(message: str, **meta) -> None:
    if meta:
        logger.bind(**meta).error(message)
    else:
        logger.error(message)


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
