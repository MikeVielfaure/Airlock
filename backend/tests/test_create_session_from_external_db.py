"""
Starting a session directly from a BDD externe query — the same doorway a
CSV upload is, alongside it. A declared schema on the connection is a
contract: a query result that doesn't match it refuses the session (422).
"""
import json
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _external_db():
    root = tempfile.mkdtemp(prefix="fx_session_external_db_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (id INTEGER, nom TEXT, pays TEXT)")
    con.executemany("INSERT INTO clients VALUES (?, ?, ?)",
                    [(1, "ACME", "FR"), (2, "BETA", "BE")])
    con.commit()
    con.close()
    return path


def _connection(name, url):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps({"url": url}),
                                            "scope": "global", "kind": "external_db"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_session_starts_from_a_query_result():
    path = _external_db()
    _connection("session_db_v1", f"sqlite:///{path}")
    r = client.post("/api/files/from-external-db", json={
        "connection": "session_db_v1", "query": "SELECT * FROM clients", "params": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"]
    assert body["preview"]["total_rows"] == 2


def test_a_declared_schema_that_matches_is_accepted():
    path = _external_db()
    vid = _connection("session_db_v2", f"sqlite:///{path}")
    client.post(f"/api/variables/{vid}/schemas", json={
        "name": "clients", "columns": [{"name": "id", "type": "integer"},
                                       {"name": "nom", "type": "string"}]})
    r = client.post("/api/files/from-external-db", json={
        "connection": "session_db_v2", "query": "SELECT * FROM clients", "params": {},
        "schema_name": "clients"})
    assert r.status_code == 200, r.text


def test_a_query_missing_a_declared_column_is_refused():
    path = _external_db()
    vid = _connection("session_db_v3", f"sqlite:///{path}")
    client.post(f"/api/variables/{vid}/schemas", json={
        "name": "clients", "columns": [{"name": "id", "type": "integer"},
                                       {"name": "pays", "type": "string"}]})
    r = client.post("/api/files/from-external-db", json={
        "connection": "session_db_v3", "query": "SELECT id, nom FROM clients", "params": {},
        "schema_name": "clients"})
    assert r.status_code == 422
    assert "pays" in r.json()["detail"]


def test_a_wrong_type_against_a_declared_schema_is_refused():
    path = _external_db()
    vid = _connection("session_db_v4", f"sqlite:///{path}")
    client.post(f"/api/variables/{vid}/schemas", json={
        "name": "clients", "columns": [{"name": "nom", "type": "integer"}]})
    r = client.post("/api/files/from-external-db", json={
        "connection": "session_db_v4", "query": "SELECT nom FROM clients", "params": {},
        "schema_name": "clients"})
    assert r.status_code == 422
    assert "nom" in r.json()["detail"]
