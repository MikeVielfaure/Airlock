"""
Every request produces one structured (JSON-able) log line — method, path,
status, duration — so `docker logs` is greppable instead of prose.
"""
import json
import logging

from fastapi.testclient import TestClient

from app.logging_setup import JsonFormatter
from app.main import app

client = TestClient(app)


def test_a_request_produces_one_structured_access_log_line(caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        r = client.get("/api/health")
    assert r.status_code == 200

    records = [rec for rec in caplog.records if rec.name == "app.access"]
    assert records
    rec = records[-1]
    assert rec.method == "GET"
    assert rec.path == "/api/health"
    assert rec.status_code == 200
    assert rec.duration_ms >= 0


def test_json_formatter_renders_valid_json_with_extra_fields_and_no_reserved_keys():
    record = logging.LogRecord(name="app.access", level=logging.INFO, pathname=__file__,
                               lineno=1, msg="request", args=(), exc_info=None)
    record.method = "GET"
    record.status_code = 200

    line = JsonFormatter().format(record)
    parsed = json.loads(line)          # must be valid JSON, not just a Python repr

    assert parsed["message"] == "request"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.access"
    assert parsed["method"] == "GET"
    assert parsed["status_code"] == 200
    # internal LogRecord bookkeeping must not leak into the payload
    assert "args" not in parsed and "msg" not in parsed and "pathname" not in parsed


def test_json_formatter_includes_the_traceback_on_an_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(name="app.access", level=logging.ERROR, pathname=__file__,
                                   lineno=1, msg="unhandled exception", args=(),
                                   exc_info=sys.exc_info())
    parsed = json.loads(JsonFormatter().format(record))
    assert "boom" in parsed["exc_info"]
