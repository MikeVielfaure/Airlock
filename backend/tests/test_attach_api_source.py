"""
Attaching an API source to a session — a saved `api` connection point can
answer with either JSON data or a file (CSV/XLSX), chosen explicitly by
`response_kind` rather than sniffed from the response. The HTTP call itself
is monkeypatched (`api_source.call`) so this needs no real network access.
"""
import io
import json

from fastapi.testclient import TestClient

from app.main import app
from app.services import api_source

client = TestClient(app)

CSV = "NOM\nAlice\n"
FIELDS = {"NOM": {"name": ["NOM"], "type": "string"}}


def _connection(name):
    r = client.post("/api/variables", json={
        "name": name,
        "value": json.dumps({"base_url": "https://exemple.fr/api", "token": "t",
                             "auth_header": "Authorization"}),
        "scope": "global", "kind": "api"})
    assert r.status_code == 200, r.text


def _session():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_a_json_response_is_walked_to_the_data_path(monkeypatch):
    _connection("api_json_v1")
    payload = {"data": {"orders": [{"ref": "R1"}, {"ref": "R2"}]}}
    monkeypatch.setattr(api_source, "call", lambda *a, **k: (json.dumps(payload).encode(), 200))
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/api", json={
        "name": "orders", "connection": "api_json_v1", "path": "orders",
        "response_kind": "json", "data_path": "data.orders"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 2
    assert body["columns"] == ["ref"]


def test_a_csv_response_is_parsed_as_a_file(monkeypatch):
    _connection("api_csv_v1")
    monkeypatch.setattr(api_source, "call",
                        lambda *a, **k: (b"NOM;VILLE\nAlice;Paris\nBob;Lyon\n", 200))
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/api", json={
        "name": "export", "connection": "api_csv_v1", "path": "export",
        "response_kind": "csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 2
    assert set(body["columns"]) == {"NOM", "VILLE"}


def test_a_connection_of_the_wrong_kind_is_refused():
    client.post("/api/variables", json={"name": "pas_une_api_v1", "value": "1",
                                        "scope": "global", "kind": "value"})
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/api", json={
        "name": "x", "connection": "pas_une_api_v1", "path": ""})
    assert r.status_code == 404


def test_a_call_failure_surfaces_as_422(monkeypatch):
    _connection("api_fail_v1")
    def _boom(*a, **k):
        raise api_source.ApiCallError("HTTP 500 en appelant https://exemple.fr/api")
    monkeypatch.setattr(api_source, "call", _boom)
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/api", json={
        "name": "x", "connection": "api_fail_v1", "path": ""})
    assert r.status_code == 422
