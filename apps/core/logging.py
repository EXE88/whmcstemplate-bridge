"""Request-scoped correlation id, propagated into every log record."""

import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
