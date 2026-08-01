"""
One HTTP call against an `api`-kind connection — nothing else. No retries, no
pagination (silently retrying would make a run or an attach non-deterministic).
How the bytes are read — a JSON payload, or a CSV/XLSX file — is entirely the
caller's decision: an `api` connection point does not know itself which one
it answers with, so nothing here guesses either.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class ApiCallError(Exception):
    """The call failed, or the payload doesn't fit the shape asked of it."""


def call(url: str, method: str, headers: dict, body, timeout: float) -> tuple[bytes, int]:
    """The raw response body and HTTP status — bytes, not text, so a caller
    reading a file (CSV/XLSX) never goes through a text decode first."""
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={str(k): str(v) for k, v in headers.items()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raise ApiCallError(f"HTTP {e.code} en appelant {url}")
    except Exception as e:  # noqa: BLE001 — a network failure must name the url
        raise ApiCallError(f"{type(e).__name__} en appelant {url} : {e}")


def walk_json_path(payload, path: str):
    """Descend a dotted path into a JSON payload (`data.orders`) — an API
    almost never returns a bare list at the top level."""
    for step in (path or "").split("."):
        if not step:
            continue
        if isinstance(payload, dict):
            payload = payload.get(step)
        else:
            raise ApiCallError(f"le chemin « {path} » ne correspond pas à la réponse")
    return payload
