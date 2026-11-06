"""Structured logging.

Pipecat logs through loguru, so the whole backend uses loguru as well.
Standard-library logging (uvicorn, httpx, ...) is routed into loguru so
all output shares one format. Set LOG_JSON=true for one JSON object per line.
"""

import inspect
import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Forward standard-library log records to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Walk out of the stdlib logging frames so loguru reports the real caller.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        serialize=json_output,
        backtrace=False,
        diagnose=False,  # never dump local variables (may contain secrets)
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [_InterceptHandler()]
        std_logger.propagate = False
    # The WebRTC ICE/DTLS libraries log every candidate check at INFO, which buries
    # application logs. Keep only their warnings and errors.
    for noisy in ("aioice", "aiortc"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
