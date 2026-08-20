"""Structured JSON logging.

Before this, the only application logging in the backend was two lines in
migrate.py - everything else relied on uvicorn's default access log, so
there was no operational signal for auth failures, dispatch retries, or
upstream errors outside the request_logs table. That table is a good audit
trail but a poor incident tool: you can't tail it, and it only holds
outcomes, not the retry that preceded them.

One-line-per-event JSON so log aggregators (Fly, Loki, Datadog) can parse
without a custom grok pattern. Any extra=... keys a caller passes are merged
into the object - notably request_id, which matches the RequestLog row's
request_id so a log line and its database row are correlatable.
"""

import json
import logging
import sys

# Attributes LogRecord always carries; anything else a caller passed via
# extra=... is application context worth emitting.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent - replaces any existing root handler so repeated calls (or
    uvicorn's own setup running first) can't produce duplicate lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; clearing them and letting records
    # propagate to root means access logs get the same JSON treatment
    # instead of appearing as unstructured text alongside it.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
