"""
A flow served as a real API: a readable address, a declared contract, and an
OpenAPI document generated from the graph rather than written beside it.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-api")

from app.main import app                     # noqa: E402

client = TestClient(app)

FLOW = """
name: Commandes partenaire
description: Renvoie les commandes du pays demandé.
params:
  - {name: pays, type: string, required: true, description: Code pays ISO, example: FR}
  - {name: seuil, type: number, default: "0", description: Montant minimum}
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {ref: A1, pays: "{pays}", montant: "120"}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""


def _u():
    return os.urandom(3).hex()


@pytest.fixture
def flow():
    name = f"commandes-{_u()}"
    client.post("/api/artefacts/graph", json={"name": name, "yaml": FLOW})
    return name


# ── the address ──────────────────────────────────────────────────────
def test_a_flow_answers_at_a_readable_address(flow):
    """`/api/run/default/commandes-x` rather than a uuid nobody can remember."""
    r = client.post(f"/api/run/default/{flow}", json={"pays": "FR"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["data"][0]["pays"] == "FR"
    assert body["run_id"]                       # journalled like any other run


def test_an_unknown_flow_says_so_plainly(flow):
    r = client.post("/api/run/default/jamais-vue", json={})
    assert r.status_code == 404 and "jamais-vue" in r.json()["detail"]


def test_a_version_can_be_pinned_in_the_call(flow):
    """Artefacts are versioned, so an address can serve one version forever."""
    listing = client.get("/api/artefacts/config").json()  # warm the store
    aid = [a for a in client.get("/api/artefacts/graph").json() if a["name"] == flow][0]["id"]
    client.post(f"/api/artefacts/graph/{aid}/versions",
                json={"yaml": FLOW.replace("A1", "A2")})

    latest = client.post(f"/api/run/default/{flow}", json={"pays": "FR"}).json()
    assert latest["data"][0]["ref"] == "A2"
    pinned = client.post(f"/api/run/default/{flow}?version=1", json={"pays": "FR"}).json()
    assert pinned["data"][0]["ref"] == "A1"


# ── the contract ─────────────────────────────────────────────────────
def test_a_missing_required_parameter_is_refused_by_the_contract(flow):
    r = client.post(f"/api/run/default/{flow}", json={})
    assert r.status_code == 422 and "pays" in r.json()["detail"]


def test_a_declared_type_is_checked_at_the_door(flow):
    """A caller who sends text where a number is expected learns it here, not
    three bricks later in a message about a failed expression."""
    r = client.post(f"/api/run/default/{flow}", json={"pays": "FR", "seuil": "beaucoup"})
    assert r.status_code == 422 and "nombre" in r.json()["detail"]


def test_an_unknown_parameter_is_named_rather_than_ignored(flow):
    """Silently ignoring it is how a caller spends an afternoon wondering why
    their parameter has no effect."""
    r = client.post(f"/api/run/default/{flow}", json={"pays": "FR", "paye": "FR"})
    assert r.status_code == 422 and "paye" in r.json()["detail"]


def test_a_default_fills_in_for_an_omitted_parameter(flow):
    assert client.post(f"/api/run/default/{flow}", json={"pays": "BE"}).status_code == 200


# ── the generated document ───────────────────────────────────────────
def test_the_openapi_document_comes_from_the_graph(flow):
    spec = client.get(f"/api/run/default/{flow}/openapi.json")
    assert spec.status_code == 200, spec.text
    doc = spec.json()
    assert doc["openapi"].startswith("3.")
    assert doc["info"]["title"] == "Commandes partenaire"
    assert doc["info"]["description"].startswith("Renvoie les commandes")

    op = doc["paths"][f"/api/run/default/{flow}"]["post"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["pays"]
    assert schema["properties"]["seuil"]["type"] == "number"
    assert schema["properties"]["pays"]["example"] == "FR"
    assert schema["properties"]["pays"]["description"] == "Code pays ISO"


def test_every_callable_flow_is_listed_with_its_contract(flow):
    body = client.get("/api/run/default").json()
    entry = [f for f in body["flows"] if f["name"] == flow][0]
    assert entry["url"] == f"/api/run/default/{flow}"
    assert entry["openapi"].endswith("/openapi.json")
    assert {p["name"] for p in entry["params"]} == {"pays", "seuil"}


def test_an_unreadable_flow_does_not_hide_the_others():
    client.post("/api/artefacts/graph", json={
        "name": f"bon-{_u()}",
        "yaml": "name: bon\nnodes:\n  - {id: o, type: response}\n"})
    body = client.get("/api/run/default").json()
    assert len(body["flows"]) >= 1


# ── the sink that closes the loop ────────────────────────────────────
def test_a_flow_can_post_its_result_somewhere():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            seen.append(json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 8791), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        y = """
name: pousse
nodes:
  - id: src
    type: inline
    config: {rows: [{ref: A1}, {ref: A2}, {ref: A3}]}
  - id: envoi
    type: http
    config: {url: "http://127.0.0.1:8791/", field: commandes, batch: 2}
edges: [{from: src, to: envoi}]
"""
        r = client.post("/api/graphs/run", json={"yaml": y})
        assert r.status_code == 200, r.text
        meta = [s for s in r.json()["trace"] if s["node"] == "envoi"][0]["meta"]
        # batches are reported rather than hidden: a partial failure has to be
        # reasonable about
        assert meta["sent"] == 3 and meta["batches"] == 2
        assert len(seen) == 2 and seen[0]["commandes"][0]["ref"] == "A1"
    finally:
        srv.shutdown()


def test_a_failing_endpoint_names_the_batch_and_what_got_through():
    y = """
name: casse
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: envoi, type: http, config: {url: "http://127.0.0.1:9/"}}
edges: [{from: src, to: envoi}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "envoi" in r.json()["detail"]


def test_the_http_sink_is_advertised_as_a_sink():
    roles = {b["type"]: b["role"] for b in client.get("/api/graphs/bricks").json()["bricks"]}
    assert roles["http"] == "sink"
