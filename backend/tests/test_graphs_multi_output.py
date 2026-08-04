"""
A graph naming two or more nodes in `outputs` opens each as its own tab when
adopted — one run, several sessions, since every node's result is already
computed by the time the graph finishes (nothing is executed twice).
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_multi_output_graph_opens_two_sessions():
    y = """
name: deux-tables
nodes:
  - id: a
    type: inline
    config:
      rows:
        - {client: ACME, montant: "100"}
        - {client: BETA, montant: "80"}
  - id: b
    type: inline
    config:
      rows:
        - {client: ACME, secteur: industrie}
outputs: [a, b]
"""
    r = client.post("/api/graphs/adopt", json={"yaml": y})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sessions" in body and len(body["sessions"]) == 2

    sid_a, sid_b = (s["session_id"] for s in body["sessions"])
    assert body["sessions"][0]["preview"]["total_rows"] == 2
    assert body["sessions"][1]["preview"]["total_rows"] == 1

    # each is an ordinary, independent session afterwards
    prev_a = client.get(f"/api/files/{sid_a}/preview").json()
    prev_b = client.get(f"/api/files/{sid_b}/preview").json()
    assert "montant" in prev_a["columns"]
    assert "secteur" in prev_b["columns"]


def test_outputs_naming_an_unknown_node_is_422():
    y = """
name: mauvaise-sortie
nodes:
  - {id: a, type: inline, config: {rows: [{x: "1"}]}}
outputs: [nope]
"""
    r = client.post("/api/graphs/adopt", json={"yaml": y})
    assert r.status_code == 422


def test_a_single_item_outputs_list_still_behaves_like_one_session():
    y = """
name: une-seule
nodes:
  - {id: a, type: inline, config: {rows: [{x: "1"}]}}
outputs: [a]
"""
    r = client.post("/api/graphs/adopt", json={"yaml": y})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sessions" not in body
    assert "session_id" in body
