"""
The "source" artefact kind — a saved recipe (dataset / external_db / api)
reloadable into another session and, new here, referenced from a Flow so it
auto-attaches at run time for the flow's own sql_computed blocks to join
against. Also covers the regression this feature required fixing: a flow
used to silently drop a computed artefact's sql_computed blocks entirely.
"""
import io
import json
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CONFIG_YAML = """
type: CSV
delimiter: ";"
Fields:
  - name: [NOM]
    type: string
"""


def _mk_config(name="conf-src-art"):
    r = client.post("/api/artefacts/config", json={"name": name, "yaml": CONFIG_YAML})
    assert r.status_code == 201, r.text
    return r.json()


def _mk_dataset_with_rows(rows, name="src-art-ds"):
    from app.db import session_scope
    from app import repository as repo
    import uuid
    with session_scope() as s:
        ds = repo.create_dataset(s, f"{name}-{uuid.uuid4().hex[:8]}",
                                 {"columns": list(rows[0].keys()), "types": {}})
        repo.insert_rows(s, ds.id, [{"key_hash": None, "data": r} for r in rows])
        s.commit()
        return ds.id


def _run(flow_id: str, content: str, fname="in.csv"):
    return client.post(f"/api/flows/{flow_id}/run",
                       files={"file": (fname, io.BytesIO(content.encode()), "text/csv")})


# ── artefact CRUD / structural validation ──────────────────────────────

def test_source_dataset_recipe_lifecycle():
    ds_id = _mk_dataset_with_rows([{"NOM": "ACME", "VILLE": "PARIS"}], "src-life")
    r = client.post("/api/artefacts/source", json={
        "name": "src-dataset-a", "body": {"source_kind": "dataset", "name": "clients_int", "dataset_id": ds_id}})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["kind"] == "source" and a["latest_version_no"] == 1

    v = client.get(f"/api/artefacts/source/{a['id']}/versions/1").json()
    assert v["body"] == {"source_kind": "dataset", "name": "clients_int", "dataset_id": ds_id}

    assert client.delete(f"/api/artefacts/source/{a['id']}").status_code == 200
    names = [x["name"] for x in client.get("/api/artefacts/source").json()]
    assert "src-dataset-a" not in names
    assert client.post(f"/api/artefacts/source/{a['id']}/restore").status_code == 200
    names = [x["name"] for x in client.get("/api/artefacts/source").json()]
    assert "src-dataset-a" in names


def test_source_recipe_needs_a_name():
    r = client.post("/api/artefacts/source", json={
        "name": "src-noname", "body": {"source_kind": "dataset", "dataset_id": "whatever"}})
    assert r.status_code == 422
    assert "name" in r.json()["detail"]


def test_dataset_source_needs_dataset_id():
    r = client.post("/api/artefacts/source", json={
        "name": "src-nodsid", "body": {"source_kind": "dataset", "name": "x"}})
    assert r.status_code == 422


def test_external_db_source_needs_connection_and_query():
    r = client.post("/api/artefacts/source", json={
        "name": "src-noconn", "body": {"source_kind": "external_db", "name": "x", "query": "SELECT 1"}})
    assert r.status_code == 422
    r2 = client.post("/api/artefacts/source", json={
        "name": "src-noquery", "body": {"source_kind": "external_db", "name": "x", "connection": "conn"}})
    assert r2.status_code == 422


def test_api_source_needs_connection():
    r = client.post("/api/artefacts/source", json={
        "name": "src-noapiconn", "body": {"source_kind": "api", "name": "x"}})
    assert r.status_code == 422


def test_unknown_source_kind_rejected():
    r = client.post("/api/artefacts/source", json={
        "name": "src-badkind", "body": {"source_kind": "carrier_pigeon", "name": "x"}})
    assert r.status_code == 422


def test_flow_source_artefact_wrong_kind_guard():
    conf = _mk_config("conf-src-guard")
    computed = client.post("/api/artefacts/computed", json={
        "name": "comp-src-guard",
        "computed": [{"name": "x", "expression": '"y"'}]}).json()
    r = client.post("/api/flows", json={
        "name": "flow-src-guard", "config_artefact_id": conf["id"],
        "source_artefact_id": computed["id"]})   # a computed, not a source
    assert r.status_code == 409


# ── the actual join at flow-run time ───────────────────────────────────

def test_flow_run_joins_a_saved_dataset_source():
    conf = _mk_config("conf-src-join")
    ds_id = _mk_dataset_with_rows([{"NOM": "ACME", "VILLE": "PARIS"}], "src-join-ds")
    src = client.post("/api/artefacts/source", json={
        "name": "src-join-a",
        "body": {"source_kind": "dataset", "name": "clients_int", "dataset_id": ds_id}}).json()
    comp = client.post("/api/artefacts/computed", json={
        "name": "comp-src-join", "computed": [],
        "sql_computed": [{"name": "VILLE_JOINTE", "mode": "replace",
                          "expression": "SELECT self._row_id, clients_int.VILLE AS VILLE_JOINTE "
                                       "FROM self LEFT JOIN clients_int ON self.NOM = clients_int.NOM"}]}).json()
    flow = client.post("/api/flows", json={
        "name": "flow-src-join", "config_artefact_id": conf["id"],
        "computed_artefact_id": comp["id"], "source_artefact_id": src["id"]}).json()
    assert flow["source_artefact_id"] == src["id"]

    r = _run(flow["id"], "NOM\nACME\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    exp = client.get(f"/api/runs/{body['run_id']}/export")
    assert exp.status_code == 200
    assert "VILLE_JOINTE" in exp.text and "PARIS" in exp.text


def test_flow_run_joins_a_saved_external_db_source():
    root = tempfile.mkdtemp(prefix="fx_source_artefact_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (nom TEXT, ville TEXT)")
    con.executemany("INSERT INTO clients VALUES (?, ?)", [("ACME", "LYON"), ("BETA", "NICE")])
    con.commit()
    con.close()

    r = client.post("/api/variables", json={
        "name": "entrepot_src_artefact_v1", "value": json.dumps({"url": f"sqlite:///{path}"}),
        "scope": "global", "kind": "external_db"})
    assert r.status_code == 200, r.text

    conf = _mk_config("conf-src-db-join")
    src = client.post("/api/artefacts/source", json={
        "name": "src-db-join-a",
        "body": {"source_kind": "external_db", "name": "clients_ext",
                 "connection": "entrepot_src_artefact_v1", "query": "SELECT nom, ville FROM clients"}}).json()
    comp = client.post("/api/artefacts/computed", json={
        "name": "comp-src-db-join", "computed": [],
        "sql_computed": [{"name": "VILLE_EXT", "mode": "replace",
                          "expression": "SELECT self._row_id, clients_ext.ville AS VILLE_EXT "
                                       "FROM self LEFT JOIN clients_ext ON self.NOM = clients_ext.nom"}]}).json()
    flow = client.post("/api/flows", json={
        "name": "flow-src-db-join", "config_artefact_id": conf["id"],
        "computed_artefact_id": comp["id"], "source_artefact_id": src["id"]}).json()

    r = _run(flow["id"], "NOM\nACME\n")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text
    exp = client.get(f"/api/runs/{r.json()['run_id']}/export")
    assert "VILLE_EXT" in exp.text and "LYON" in exp.text


# ── a flow itself as a source: lazily re-run, never a kept-open session ──

CONFIG_YAML_NV = """
type: CSV
delimiter: ";"
Fields:
  - name: [NOM]
    type: string
  - name: [VILLE]
    type: string
"""


def _mk_flow_with_fixed_source(name, rows, computed_id=None):
    """A flow with its own config + fixed dataset source — everything a
    flow needs to serve as another flow's source (no file upload possible
    in that automated context). Returns (flow, dataset_id) — the caller
    must clean up via `_forget_flow_and_dataset` (see test_store.py's
    identical concern: `source_dataset_id` is a real FK, and another
    file's fixture wipes all datasets between tests)."""
    conf = client.post("/api/artefacts/config", json={"name": f"conf-{name}", "yaml": CONFIG_YAML_NV}).json()
    ds_id = _mk_dataset_with_rows(rows, f"ds-{name}")
    body = {"name": name, "config_artefact_id": conf["id"], "source_dataset_id": ds_id}
    if computed_id:
        body["computed_artefact_id"] = computed_id
    flow = client.post("/api/flows", json=body).json()
    assert flow["source_dataset_id"] == ds_id, flow
    return flow, ds_id


def _forget_flow_and_dataset(flow_id, dataset_id):
    """Identical to test_store.py's helper of the same name — needed here
    for the same reason: a flow's `source_dataset_id` is a real FK, and
    test_table_rights.py wipes all datasets between its own tests."""
    from app.db import session_scope
    from app.db_models import Dataset, DatasetRow, Flow, Run
    with session_scope() as s:
        s.query(Run).filter(Run.flow_id == flow_id).delete()
        s.flush()
        s.query(Flow).filter(Flow.id == flow_id).delete()
        s.flush()
        s.query(DatasetRow).filter(DatasetRow.dataset_id == dataset_id).delete()
        s.flush()
        s.query(Dataset).filter(Dataset.id == dataset_id).delete()
        s.commit()


def test_flow_source_requires_the_referenced_flow_to_have_a_fixed_input():
    conf = _mk_config("conf-src-flow-nofixed")
    inner = client.post("/api/flows", json={
        "name": "flow-inner-nofixed", "config_artefact_id": conf["id"]}).json()   # no source_dataset_id
    src = client.post("/api/artefacts/source", json={
        "name": "src-flow-nofixed", "body": {"source_kind": "flow", "name": "inner", "flow_id": inner["id"]}}).json()
    outer_conf = _mk_config("conf-src-flow-outer-nofixed")
    outer = client.post("/api/flows", json={
        "name": "flow-outer-nofixed", "config_artefact_id": outer_conf["id"], "source_artefact_id": src["id"]}).json()

    r = _run(outer["id"], "NOM\nACME\n")
    assert r.status_code == 422, r.text
    assert "n'a pas de source fixe" in r.json()["detail"]


def test_flow_as_source_runs_the_inner_flow_and_joins_its_processed_output():
    comp = client.post("/api/artefacts/computed", json={
        "name": "comp-inner-flow-src",
        "computed": [{"name": "VILLE_MAJ", "expression": "UPPER([VILLE])"}]}).json()
    inner, inner_ds_id = _mk_flow_with_fixed_source(
        "flow-inner-src", [{"NOM": "ACME", "VILLE": "paris"}], computed_id=comp["id"])

    src = client.post("/api/artefacts/source", json={
        "name": "src-flow-a", "body": {"source_kind": "flow", "name": "inner_flow", "flow_id": inner["id"]}}).json()
    outer_comp = client.post("/api/artefacts/computed", json={
        "name": "comp-outer-flow-src", "computed": [],
        "sql_computed": [{"name": "VILLE_MAJ_JOINTE", "mode": "replace",
                          "expression": "SELECT self._row_id, inner_flow.VILLE_MAJ AS VILLE_MAJ_JOINTE "
                                       "FROM self LEFT JOIN inner_flow ON self.NOM = inner_flow.NOM"}]}).json()
    outer_conf = _mk_config("conf-outer-flow-src")
    outer = client.post("/api/flows", json={
        "name": "flow-outer-src", "config_artefact_id": outer_conf["id"],
        "computed_artefact_id": outer_comp["id"], "source_artefact_id": src["id"]}).json()

    try:
        runs_before = client.get("/api/runs", params={"flow_id": inner["id"]}).json()
        r = _run(outer["id"], "NOM\nACME\n")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True, r.text
        exp = client.get(f"/api/runs/{r.json()['run_id']}/export")
        assert "VILLE_MAJ_JOINTE" in exp.text and "PARIS" in exp.text   # proves the inner flow's
                                                                         # own UPPER() computed column ran,
                                                                         # not just its raw dataset

        # The inner flow's own execution is a real, audited run too.
        runs_after = client.get("/api/runs", params={"flow_id": inner["id"]}).json()
        assert len(runs_after) == len(runs_before) + 1
    finally:
        _forget_flow_and_dataset(inner["id"], inner_ds_id)


def test_flow_source_self_reference_is_refused():
    conf = _mk_config("conf-src-flow-self")
    flow = client.post("/api/flows", json={"name": "flow-self-src", "config_artefact_id": conf["id"]}).json()
    src = client.post("/api/artefacts/source", json={
        "name": "src-flow-self", "body": {"source_kind": "flow", "name": "moi_meme", "flow_id": flow["id"]}}).json()
    assert client.patch(f"/api/flows/{flow['id']}",
                        json={"source_artefact_id": src["id"]}).status_code == 200

    r = _run(flow["id"], "NOM\nACME\n")
    assert r.status_code == 422, r.text
    assert "circulaire" in r.json()["detail"]


def test_flow_source_two_flow_cycle_is_refused():
    # Both flows need a real fixed source of their own — otherwise the
    # recursion never gets deep enough to find the cycle, it just stops
    # earlier at "no fixed source" (covered by its own test above).
    flow_a, ds_a = _mk_flow_with_fixed_source("flow-cycle-a", [{"NOM": "ACME", "VILLE": "paris"}])
    flow_b, ds_b = _mk_flow_with_fixed_source("flow-cycle-b", [{"NOM": "ACME", "VILLE": "lyon"}])

    try:
        src_to_b = client.post("/api/artefacts/source", json={
            "name": "src-cycle-to-b", "body": {"source_kind": "flow", "name": "vers_b", "flow_id": flow_b["id"]}}).json()
        assert client.patch(f"/api/flows/{flow_a['id']}",
                            json={"source_artefact_id": src_to_b["id"]}).status_code == 200

        src_to_a = client.post("/api/artefacts/source", json={
            "name": "src-cycle-to-a", "body": {"source_kind": "flow", "name": "vers_a", "flow_id": flow_a["id"]}}).json()
        assert client.patch(f"/api/flows/{flow_b['id']}",
                            json={"source_artefact_id": src_to_a["id"]}).status_code == 200

        r = _run(flow_a["id"], "NOM\nACME\n")
        assert r.status_code == 422, r.text
        assert "circulaire" in r.json()["detail"]
    finally:
        _forget_flow_and_dataset(flow_a["id"], ds_a)
        _forget_flow_and_dataset(flow_b["id"], ds_b)


def test_attach_flow_source_in_an_interactive_session():
    inner, inner_ds_id = _mk_flow_with_fixed_source("flow-inner-attach", [{"NOM": "ACME", "VILLE": "lyon"}])
    try:
        sid = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(b"NOM\nACME\n"), "text/csv")},
                          data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"}).json()["session_id"]
        r = client.post(f"/api/files/{sid}/sources/flow", json={"name": "inner_attached", "flow_id": inner["id"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["row_count"] == 1
        assert set(body["columns"]) == {"NOM", "VILLE"}

        r2 = client.post(f"/api/files/{sid}/process", json={
            "visible_cols": ["NOM"], "fields": {"NOM": {"name": ["NOM"], "type": "string"}},
            "sql_computed": [{"name": "VILLE", "mode": "replace",
                              "expression": "SELECT self._row_id, inner_attached.VILLE "
                                           "FROM self LEFT JOIN inner_attached ON self.NOM = inner_attached.NOM"}]})
        assert r2.status_code == 200, r2.text
        ville_col = r2.json()["columns"].index("VILLE")
        assert r2.json()["data"][0][ville_col] == "lyon"
    finally:
        _forget_flow_and_dataset(inner["id"], inner_ds_id)


def test_flow_sql_computed_without_a_source_artefact_now_runs():
    """Regression: `resolve_flow` used to read only `computed` from a
    referenced computed artefact and silently drop `sql_computed` — a flow
    could not benefit from a self-only SQL block (e.g. a window function)
    at all, even with nothing to join. Fixed as part of wiring source
    artefacts through; this must keep working with no source attached."""
    conf = _mk_config("conf-src-selfonly")
    comp = client.post("/api/artefacts/computed", json={
        "name": "comp-src-selfonly", "computed": [],
        "sql_computed": [{"name": "RANG", "mode": "replace",
                          "expression": "SELECT _row_id, RANK() OVER (ORDER BY NOM) AS RANG FROM self"}]}).json()
    flow = client.post("/api/flows", json={
        "name": "flow-src-selfonly", "config_artefact_id": conf["id"],
        "computed_artefact_id": comp["id"]}).json()
    assert flow["source_artefact_id"] is None

    r = _run(flow["id"], "NOM\nBETA\nACME\n")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text
    exp = client.get(f"/api/runs/{r.json()['run_id']}/export")
    assert "RANG" in exp.text
