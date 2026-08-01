"""
Known tables/endpoints declared on a connection point — reusable, and a
real contract: a schema declared and then not matched by the actual result
refuses the source (422), never a silent partial accept.
"""
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _connection(name, kind, value):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps(value),
                                            "scope": "global", "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_schema_can_be_declared_listed_and_removed():
    vid = _connection("schema_db_v1", "external_db", {"url": "sqlite:///nope.db"})
    r = client.post(f"/api/variables/{vid}/schemas", json={
        "name": "clients", "columns": [{"name": "id", "type": "integer"},
                                       {"name": "nom", "type": "string"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "clients"
    assert {c["name"] for c in body["columns"]} == {"id", "nom"}

    r = client.get(f"/api/variables/{vid}/schemas")
    assert r.status_code == 200, r.text
    assert [s["name"] for s in r.json()] == ["clients"]

    r = client.delete(f"/api/variables/{vid}/schemas/clients")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/variables/{vid}/schemas").json() == []


def test_saving_a_schema_twice_updates_it_in_place():
    vid = _connection("schema_db_v2", "external_db", {"url": "sqlite:///nope.db"})
    client.post(f"/api/variables/{vid}/schemas",
               json={"name": "t", "columns": [{"name": "a", "type": "string"}]})
    client.post(f"/api/variables/{vid}/schemas",
               json={"name": "t", "columns": [{"name": "a", "type": "string"},
                                              {"name": "b", "type": "integer"}]})
    rows = client.get(f"/api/variables/{vid}/schemas").json()
    assert len(rows) == 1
    assert {c["name"] for c in rows[0]["columns"]} == {"a", "b"}
