"""
Starting a session directly from a saved API connection's answer — JSON
data or a file, chosen explicitly. Same declared-schema contract as the
BDD externe counterpart.
"""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.services import api_source

client = TestClient(app)


def _connection(name):
    r = client.post("/api/variables", json={
        "name": name,
        "value": json.dumps({"base_url": "https://exemple.fr/api"}),
        "scope": "global", "kind": "api"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_session_starts_from_a_json_response(monkeypatch):
    _connection("session_api_v1")
    payload = {"orders": [{"ref": "R1"}, {"ref": "R2"}]}
    monkeypatch.setattr(api_source, "call", lambda *a, **k: (json.dumps(payload).encode(), 200))
    r = client.post("/api/files/from-api", json={
        "connection": "session_api_v1", "path": "orders",
        "response_kind": "json", "data_path": "orders"})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 2


def test_a_declared_schema_mismatch_refuses_the_session(monkeypatch):
    vid = _connection("session_api_v2")
    client.post(f"/api/variables/{vid}/schemas", json={
        "name": "orders", "columns": [{"name": "ref", "type": "string"},
                                      {"name": "montant", "type": "float"}],
        "path": "orders", "data_path": "orders"})
    payload = {"orders": [{"ref": "R1"}]}   # missing "montant"
    monkeypatch.setattr(api_source, "call", lambda *a, **k: (json.dumps(payload).encode(), 200))
    r = client.post("/api/files/from-api", json={
        "connection": "session_api_v2", "path": "orders",
        "response_kind": "json", "data_path": "orders", "schema_name": "orders"})
    assert r.status_code == 422
    assert "montant" in r.json()["detail"]


def test_a_session_starts_from_a_csv_response(monkeypatch):
    _connection("session_api_v3")
    monkeypatch.setattr(api_source, "call",
                        lambda *a, **k: (b"NOM;VILLE\nAlice;Paris\n", 200))
    r = client.post("/api/files/from-api", json={
        "connection": "session_api_v3", "path": "export", "response_kind": "csv"})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 1
