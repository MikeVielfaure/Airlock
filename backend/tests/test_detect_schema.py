"""
Detecting a schema: run the query/call once and propose columns + inferred
types, so declaring a known table/endpoint does not mean typing every column
by hand. This only fills the schema form — nothing is saved by this route.
"""
import json
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _external_db():
    root = tempfile.mkdtemp(prefix="fx_detect_schema_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (id INTEGER, nom TEXT, actif INTEGER)")
    con.executemany("INSERT INTO clients VALUES (?, ?, ?)",
                    [(1, "ACME", 1), (2, "BETA", 0)])
    con.commit()
    con.close()
    return path


def _connection(name, kind, value):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps(value),
                                            "scope": "global", "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_detecting_a_sql_schema_proposes_columns_and_types():
    path = _external_db()
    vid = _connection("detect_db_v1", "external_db", {"url": f"sqlite:///{path}"})
    r = client.post(f"/api/variables/{vid}/detect-schema",
                    json={"query": "SELECT * FROM clients"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c["type"] for c in r.json()["columns"]}
    assert cols["id"] == "integer"
    assert cols["nom"] == "string"


def test_detecting_with_an_empty_query_is_refused():
    path = _external_db()
    vid = _connection("detect_db_v2", "external_db", {"url": f"sqlite:///{path}"})
    r = client.post(f"/api/variables/{vid}/detect-schema", json={"query": ""})
    assert r.status_code == 422


def test_detecting_on_the_wrong_connection_kind_is_refused():
    vid = _connection("detect_notdb_v1", "smtp", {"host": "mail.example.invalid"})
    r = client.post(f"/api/variables/{vid}/detect-schema", json={"query": "SELECT 1"})
    assert r.status_code == 409


def test_detecting_an_api_schema_proposes_columns():
    vid = _connection("detect_api_v1", "api", {"base_url": "https://example.invalid"})

    def fake_call(url, method, headers, body, timeout):
        return json.dumps([{"id": "1", "label": "A"}, {"id": "2", "label": "B"}]).encode(), 200

    from app.services import api_source
    old_call = api_source.call
    api_source.call = fake_call
    try:
        r = client.post(f"/api/variables/{vid}/detect-schema",
                        json={"path": "items", "method": "GET"})
        assert r.status_code == 200, r.text
        names = {c["name"] for c in r.json()["columns"]}
        assert names == {"id", "label"}
    finally:
        api_source.call = old_call
