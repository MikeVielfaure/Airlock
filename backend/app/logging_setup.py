"""
Structured logging — one JSON object per line to stdout, so `docker logs`
(or anything ingesting them) gets greppable, machine-parseable records
instead of prose. Configured once, at import time, before the request
middleware or anything else in the app logs a line.
"""
from __future__ import annotations

import json
import logging
import os
import sys

# Attributes `logging.LogRecord` always carries — everything else on a record
# was passed via `extra={...}` by the caller and belongs in the JSON payload.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure_logging() -> None:
    """Idempotent — importing this twice (a test run, a hot reload) must not
    double up handlers and duplicate every line."""
    global _configured
    if _configured:
        return
    _configured = True
    level = os.environ.get("FX_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
