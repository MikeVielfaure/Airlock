"""
A single-column join key, declared once on an already-attached source, lets
a plain computed column address it as [name.field] — no SQL block needed —
while an ambiguous key (duplicate values on the source side) surfaces as
"#ERR" per row rather than a silently-guessed value.
"""
import io

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


def _mk_config(name="conf-key"):
    r = client.post("/api/artefacts/config", json={"name": name, "yaml": CONFIG_YAML})
    assert r.status_code == 201, r.text
    return r.json()


def _mk_dataset_with_rows(rows, name="ds-key"):
    from app.db import session_scope
    from app import repository as repo
    import uuid
    with session_scope() as s:
        ds = repo.create_dataset(s, f"{name}-{uuid.uuid4().hex[:8]}",
                                 {"columns": list(rows[0].keys()), "types": {}})
        repo.insert_rows(s, ds.id, [{"key_hash": None, "data": r} for r in rows])
        s.commit()
        return ds.id


def _session():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(b"NOM\nACME\nBETA\n"), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# ── the key route itself ───────────────────────────────────────────────

def test_set_key_on_unknown_source_is_404():
    sid = _session()
    r = client.put(f"/api/files/{sid}/sources/nope/key", json={"local_column": "NOM", "source_column": "nom"})
    assert r.status_code == 404


def test_set_key_with_unknown_source_column_is_422():
    sid = _session()
    ds_id = _mk_dataset_with_rows([{"nom": "ACME", "ville": "Paris"}], "ds-badcol")
    client.post(f"/api/files/{sid}/sources/dataset", json={"name": "clients", "dataset_id": ds_id})
    r = client.put(f"/api/files/{sid}/sources/clients/key",
                   json={"local_column": "NOM", "source_column": "does_not_exist"})
    assert r.status_code == 422


def test_set_and_clear_key():
    sid = _session()
    ds_id = _mk_dataset_with_rows([{"nom": "ACME", "ville": "Paris"}], "ds-setclear")
    client.post(f"/api/files/{sid}/sources/dataset", json={"name": "clients", "dataset_id": ds_id})
    r = client.put(f"/api/files/{sid}/sources/clients/key",
                   json={"local_column": "NOM", "source_column": "nom"})
    assert r.status_code == 200, r.text
    assert client.delete(f"/api/files/{sid}/sources/clients/key").status_code == 200


# ── the lookup itself, through /process ─────────────────────────────────

def test_plain_computed_column_looks_up_an_attached_keyed_source():
    sid = _session()
    ds_id = _mk_dataset_with_rows(
        [{"nom": "ACME", "ville": "Paris"}, {"nom": "BETA", "ville": "Lyon"}], "ds-lookup")
    client.post(f"/api/files/{sid}/sources/dataset", json={"name": "clients", "dataset_id": ds_id})
    client.put(f"/api/files/{sid}/sources/clients/key",
              json={"local_column": "NOM", "source_column": "nom"})

    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM"], "fields": {"NOM": {"name": ["NOM"], "type": "string"}},
        "computed": [{"name": "VILLE", "expression": '[clients.ville]'}]})
    assert r.status_code == 200, r.text
    body = r.json()
    ville_col = body["columns"].index("VILLE")
    assert body["data"][0][ville_col] == "Paris"
    assert body["data"][1][ville_col] == "Lyon"
    assert body["compute_errors"] == {}


def test_lookup_with_no_declared_key_is_silently_empty():
    sid = _session()
    ds_id = _mk_dataset_with_rows([{"nom": "ACME", "ville": "Paris"}], "ds-nokey")
    client.post(f"/api/files/{sid}/sources/dataset", json={"name": "clients", "dataset_id": ds_id})
    # no PUT .../key at all

    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM"], "fields": {"NOM": {"name": ["NOM"], "type": "string"}},
        "computed": [{"name": "VILLE", "expression": '[clients.ville]'}]})
    assert r.status_code == 200, r.text
    body = r.json()
    ville_col = body["columns"].index("VILLE")
    assert body["data"][0][ville_col] == ""
    assert body["compute_errors"] == {}


def test_ambiguous_key_surfaces_as_row_level_error_not_a_guess():
    sid = _session()
    ds_id = _mk_dataset_with_rows(
        [{"nom": "ACME", "ville": "Paris"}, {"nom": "ACME", "ville": "Lyon"}], "ds-ambiguous")
    client.post(f"/api/files/{sid}/sources/dataset", json={"name": "clients", "dataset_id": ds_id})
    client.put(f"/api/files/{sid}/sources/clients/key",
              json={"local_column": "NOM", "source_column": "nom"})

    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM"], "fields": {"NOM": {"name": ["NOM"], "type": "string"}},
        "computed": [{"name": "VILLE", "expression": '[clients.ville]'}]})
    assert r.status_code == 200, r.text
    body = r.json()
    ville_col = body["columns"].index("VILLE")
    assert body["data"][0][ville_col] == "#ERR"


# ── the recipe carries the key, so it travels with a saved "source" ────

def test_source_recipe_join_key_requires_both_fields_together():
    r = client.post("/api/artefacts/source", json={
        "name": "src-halfkey",
        "body": {"source_kind": "dataset", "name": "clients", "dataset_id": "whatever", "join_local": "NOM"}})
    assert r.status_code == 422


def test_flow_with_keyed_source_recipe_resolves_plain_computed_lookup():
    # No source_dataset_id here (only source_artefact_id, whose body carries
    # dataset_id as JSON, not a real FK) — no leaked-dataset cleanup needed,
    # unlike the flow-as-source tests in test_source_artefact.py.
    ds_id = _mk_dataset_with_rows([{"nom": "ACME", "ville": "Paris"}], "ds-flowkey")
    src = client.post("/api/artefacts/source", json={
        "name": "src-flowkey", "body": {"source_kind": "dataset", "name": "clients",
                                        "dataset_id": ds_id, "join_local": "NOM", "join_source": "nom"}}).json()
    comp = client.post("/api/artefacts/computed", json={
        "name": "comp-flowkey", "computed": [{"name": "VILLE", "expression": "[clients.ville]"}]}).json()
    conf = _mk_config("conf-flowkey")
    flow = client.post("/api/flows", json={
        "name": "flow-key-lookup", "config_artefact_id": conf["id"],
        "computed_artefact_id": comp["id"], "source_artefact_id": src["id"]}).json()

    r = client.post(f"/api/flows/{flow['id']}/run",
                    files={"file": ("f.csv", io.BytesIO(b"NOM\nACME\n"), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text
    exp = client.get(f"/api/runs/{r.json()['run_id']}/export")
    assert "VILLE" in exp.text and "Paris" in exp.text
