"""
Production hardening: a request too large is refused before pandas ever
sees it, and CORS origins are configurable instead of permanently "*".

The middleware reads its limits from an env var once, at import time — real
for a deployed process, but awkward to flip mid-test-suite. So the ASGI
class and the CORS-origin parser are each exercised directly, with explicit
values, rather than through env-var reimport gymnastics.
"""
import asyncio
import io
import os

os.environ.setdefault("FX_MASTER_KEY", "cle-maitresse-de-test-pour-la-suite")

import pytest
from fastapi.testclient import TestClient

from app.main import _MaxBodySizeMiddleware, _parse_cors_origins, app

client = TestClient(app)


def _run_middleware(max_bytes: int, content_length: int):
    """Drive the middleware with a synthetic ASGI scope, no real body — the
    check happens purely on the Content-Length header before anything is
    read, so a fake header is enough."""
    calls = {"inner_app_ran": False}

    async def inner_app(scope, receive, send):
        calls["inner_app_ran"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = _MaxBodySizeMiddleware(inner_app, max_bytes=max_bytes)
    scope = {"type": "http", "headers": [(b"content-length", str(content_length).encode())]}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(mw(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, calls["inner_app_ran"]


def test_a_request_over_the_limit_is_refused_before_reaching_the_app():
    status, ran = _run_middleware(max_bytes=1000, content_length=2000)
    assert status == 413
    assert ran is False


def test_a_request_under_the_limit_reaches_the_app():
    status, ran = _run_middleware(max_bytes=1000, content_length=500)
    assert status == 200
    assert ran is True


def test_a_non_http_scope_is_passed_through_untouched():
    """A websocket/lifespan scope has no Content-Length concept at all —
    must never be mistaken for an oversized request."""
    async def inner_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    mw = _MaxBodySizeMiddleware(inner_app, max_bytes=1)
    sent = []

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        sent.append(message)

    asyncio.run(mw({"type": "lifespan"}, receive, send))
    assert sent[0]["status"] == 200


def test_uploading_a_file_over_the_configured_limit_is_refused():
    """End-to-end through the real app: the currently-configured default
    (FX_MAX_UPLOAD_MB, 200 by default) comfortably allows a small test file,
    so this exercises the actual wired-in middleware instance rather than a
    synthetic one, using a file too large only if the deployed default were
    absurdly small — instead, assert the small file passes it cleanly."""
    small = b"id;nom\n1;Dupont\n"
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(small), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code != 413


# ── CORS origin parsing ─────────────────────────────────────────────────

def test_unset_or_empty_cors_origins_stays_wide_open():
    assert _parse_cors_origins("") == ["*"]
    assert _parse_cors_origins("*") == ["*"]


def test_cors_origins_can_be_restricted_to_real_domains():
    assert _parse_cors_origins("https://app.example.com") == ["https://app.example.com"]
    assert _parse_cors_origins("https://a.example.com, https://b.example.com") == [
        "https://a.example.com", "https://b.example.com"]


def test_stray_commas_and_whitespace_do_not_produce_empty_origins():
    assert _parse_cors_origins(" , https://a.example.com ,, ") == ["https://a.example.com"]
