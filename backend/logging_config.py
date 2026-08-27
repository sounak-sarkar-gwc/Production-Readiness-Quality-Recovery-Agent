"""Structured-ish logging: every line carries a request id so concurrent
requests' log lines can be told apart, even though FastAPI runs sync
endpoints in a thread pool. Falls back to "-" for log lines emitted outside
a request (e.g. at startup)."""

import contextvars
import logging
import sys

from .config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_RequestIdFilter())

    root.handlers = [handler]

    # Quiet down noisy third-party loggers unless we're actually debugging.
    if settings.log_level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
