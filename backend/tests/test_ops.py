"""
Connection points and the operations table: what a run uses, and what became
of it — including the ability to run it again, two different ways.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FLOW = """
name: flux-observe
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}, {a: "2"}]}}
  - {id: dire, type: log, config: {message: "{rows} ligne(s) pour {client}"}}
  - {id: out, type: response}
edges: [{from: src, to: dire}, {from: dire, to: out}]
"""


def _var(**kw):
    return client.post("/api/variables", json=kw)


# ── connection points ────────────────────────────────────────────────
def test_the_cascade_resolves_most_specific_first():
    gid = client.post("/api/artefacts/graph",
                      json={"name": "flux-vars", "yaml": FLOW}).json()["id"]
    _var(name="api_base", value="https://global", scope="global")
    _var(name="api_base", value="https://rh", scope="environment", environment="rh")
    _var(name="api_base", value="https://sandbox", scope="flow", graph_id=gid)
    _var(name="api_base", value="https://brique", scope="brick", graph_id=gid, node_id="src")

    seen = client.get("/api/variables/resolved").json()["variables"]
    assert seen["api_base"] == "https://global"

    rh = client.get("/api/variables/resolved?env=rh").json()["variables"]
    assert rh["api_base"] == "https://rh"

    flow = client.get(f"/api/variables/resolved?env=rh&graph_id={gid}").json()["variables"]
    assert flow["api_base"] == "https://sandbox"

    brick = client.get(
        f"/api/variables/resolved?env=rh&graph_id={gid}&node_id=src").json()["variables"]
    assert brick["api_base"] == "https://brique"


def test_a_narrow_scope_must_say_what_it_narrows():
    assert _var(name="x", value="1", scope="flow").status_code == 409
    assert _var(name="x", value="1", scope="brick", graph_id="g").status_code == 409
    assert _var(name="x", value="1", scope="inconnu").status_code == 409


def test_a_secret_never_travels_to_a_screen_or_a_journal():
    _var(name="cle_api", value="SUPERSECRET1234", scope="global", secret=True)
    shown = [v for v in client.get("/api/variables").json() if v["name"] == "cle_api"][0]
    assert shown["value"] != "SUPERSECRET1234"

    res = client.get("/api/variables/resolved").json()
    assert res["variables"]["cle_api"] != "SUPERSECRET1234"
    assert "cle_api" in res["secret_names"]

    # and a message that pasted the secret in is masked in the journal
    y = """
name: fuite
nodes:
  - {id: s, type: inline, config: {rows: [{a: "1"}]}}
  - {id: l, type: log, config: {message: "appel {cle_api}"}}
  - {id: o, type: response}
edges: [{from: s, to: l}, {from: l, to: o}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    run = client.get(f"/api/ops/runs/{r.json()['run_id']}").json()
    assert "SUPERSECRET1234" not in str(run)


def test_variables_substitute_into_configs_and_params_win():
    _var(name="client", value="ACME", scope="global")
    r = client.post("/api/graphs/run", json={"yaml": FLOW})
    assert r.status_code == 200, r.text
    run = client.get(f"/api/ops/runs/{r.json()['run_id']}").json()
    assert run["messages"][0]["text"] == "2 ligne(s) pour ACME"

    # an explicit call argument is more specific than a stored value
    y = FLOW.replace("name: flux-observe", "name: flux-observe\nparams: [{name: client, default: X}]")
    r2 = client.post("/api/graphs/run", json={"yaml": y, "params": {"client": "BETA"}})
    run2 = client.get(f"/api/ops/runs/{r2.json()['run_id']}").json()
    assert "BETA" in run2["messages"][0]["text"]


# ── the operations table ─────────────────────────────────────────────
def test_every_run_is_journalled_with_its_steps():
    r = client.post("/api/graphs/run", json={"yaml": FLOW})
    run = client.get(f"/api/ops/runs/{r.json()['run_id']}").json()
    assert run["status"] == "success"
    assert run["rows_out"] == 2
    assert [s["node_id"] for s in run["steps"]] == ["src", "dire", "out"]
    assert all("ms" in s for s in run["steps"])


def test_a_failed_run_is_kept_and_names_where_it_stopped():
    y = """
name: casse
nodes:
  - {id: s, type: inline, config: {rows: [{a: "1"}]}}
  - {id: boom, type: compute, config: {columns: {x: "NOPE("}}}
  - {id: o, type: response}
edges: [{from: s, to: boom}, {from: boom, to: o}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422                     # the caller still gets the error

    listing = client.get("/api/ops/runs?status=error").json()
    assert listing["counts"]["error"] >= 1
    failed = [x for x in listing["runs"] if x["graph_name"] == "casse"][0]
    assert failed["error_node"] == "boom"
    detail = client.get(f"/api/ops/runs/{failed['id']}").json()
    assert any(s["status"] == "error" for s in detail["steps"])


def test_the_table_filters_and_counts_by_status():
    body = client.get("/api/ops/runs?limit=5").json()
    assert set(body["counts"]) == {"running", "success", "error"}
    assert len(body["runs"]) <= 5


# ── replay: two meanings, on purpose ─────────────────────────────────
def test_replaying_the_same_data_does_not_call_the_world_again():
    """The snapshot is what makes a failed run reproducible once the upstream
    payload is gone."""
    r = client.post("/api/graphs/run", json={"yaml": FLOW})
    rid = r.json()["run_id"]
    assert client.get(f"/api/ops/runs/{rid}").json()["has_snapshot"] is True

    again = client.post(f"/api/ops/runs/{rid}/replay", json={"mode": "same_data"})
    assert again.status_code == 200, again.text
    new = again.json()["run"]
    assert new["replay_of"] == rid and new["replay_mode"] == "same_data"
    assert new["status"] == "success" and new["rows_out"] == 2
    src = [s for s in new["steps"] if s["node_id"] == "src"][0]
    assert src["meta"].get("replayed") is True      # it read the snapshot


def test_replaying_with_fresh_data_re_executes_the_sources():
    r = client.post("/api/graphs/run", json={"yaml": FLOW})
    rid = r.json()["run_id"]
    again = client.post(f"/api/ops/runs/{rid}/replay", json={"mode": "refetch"})
    assert again.status_code == 200
    src = [s for s in again.json()["run"]["steps"] if s["node_id"] == "src"][0]
    assert src["meta"].get("replayed") is not True  # the source really ran


def test_a_replay_can_override_the_parameters():
    y = FLOW.replace("name: flux-observe",
                     "name: flux-observe\nparams: [{name: client, default: X}]")
    rid = client.post("/api/graphs/run", json={"yaml": y}).json()["run_id"]
    again = client.post(f"/api/ops/runs/{rid}/replay",
                        json={"mode": "same_data", "params": {"client": "CORRIGE"}})
    assert "CORRIGE" in str(again.json()["run"]["messages"])


def test_an_unknown_mode_and_a_missing_snapshot_are_refused_clearly():
    rid = client.post("/api/graphs/run", json={"yaml": FLOW}).json()["run_id"]
    assert client.post(f"/api/ops/runs/{rid}/replay", json={"mode": "nawak"}).status_code == 422
    assert client.post("/api/ops/runs/inconnu/replay", json={}).status_code == 404
