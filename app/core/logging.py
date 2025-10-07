from __future__ import annotations

from loguru import logger


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {message}",
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
