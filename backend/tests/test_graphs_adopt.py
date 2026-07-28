"""
Adopting a flow's output as an ordinary working session — the multi-source
equivalent of `POST /api/datasets/{id}/open`. A graph that ends in a `join`
(no sink) becomes exactly the kind of session Schéma & Règles, Calculs and
Rapport already know how to work with, because none of them know or care
where a session came from.
"""
import yaml
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_a_join_with_no_sink_can_be_adopted_as_a_session():
    y = """
name: croisement
nodes:
  - id: ventes
    type: inline
    config:
      rows:
        - {client: ACME, montant: "100"}
        - {client: BETA, montant: "80"}
  - id: refs
    type: inline
    config:
      rows:
        - {client: ACME, secteur: industrie}
        - {client: BETA, secteur: services}
  - id: j
    type: join
    config: {left: ventes, right: refs, on: [client], how: left}
edges: [{from: ventes, to: j}, {from: refs, to: j}]
"""
    r = client.post("/api/graphs/adopt", json={"yaml": y})
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["session_id"]
    assert body["preview"]["total_rows"] == 2
    assert "secteur" in body["preview"]["columns"]

    # The rest of the app treats this exactly like any other session.
    prev = client.get(f"/api/files/{sid}/preview").json()
    assert prev["total_rows"] == 2
    assert "industrie" in str(prev["data"])


def test_adopting_a_graph_that_fails_reports_the_failing_node():
    y = """
name: casse
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: boom, type: compute, config: {columns: {x: "NOPE("}}}
edges: [{from: src, to: boom}]
"""
    r = client.post("/api/graphs/adopt", json={"yaml": y})
    assert r.status_code == 422
    assert "boom" in r.json()["detail"]


def test_a_result_over_the_row_cap_is_refused_with_a_pointer_to_dataset_write():
    rows = [{"a": str(i)} for i in range(200_001)]
    doc = {"name": "trop-gros", "nodes": [{"id": "src", "type": "inline",
                                           "config": {"rows": rows}}]}
    r = client.post("/api/graphs/adopt", json={"yaml": yaml.safe_dump(doc)})
    assert r.status_code == 413
    assert "dataset_write" in r.json()["detail"]
