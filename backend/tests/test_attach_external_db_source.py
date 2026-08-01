"""
Attaching a BDD externe source to a session — the same bound-params, no
textual-substitution contract the `external_db` flow brick already has,
exercised against a throwaway SQLite file (not the app's own store).
"""
import io
import json
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "NOM\nACME\n"
FIELDS = {"NOM": {"name": ["NOM"], "type": "string"}}


def _external_db():
    root = tempfile.mkdtemp(prefix="fx_attach_external_db_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (id INTEGER, nom TEXT, pays TEXT)")
    con.executemany("INSERT INTO clients VALUES (?, ?, ?)",
                    [(1, "ACME", "FR"), (2, "BETA", "BE"), (3, "GAMMA", "FR")])
    con.commit()
    con.close()
    return path


def _connection(name, url):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps({"url": url}),
                                            "scope": "global", "kind": "external_db"})
    assert r.status_code == 200, r.text


def _session():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_a_bound_parameter_attaches_the_filtered_rows():
    path = _external_db()
    _connection("entrepot_attach_v1", f"sqlite:///{path}")
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/external_db", json={
        "name": "clients", "connection": "entrepot_attach_v1",
        "query": "SELECT * FROM clients WHERE pays = :pays", "params": {"pays": "FR"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 2
    assert set(body["columns"]) == {"id", "nom", "pays"}


def test_the_attached_source_can_be_joined_in_a_sql_computed_block():
    path = _external_db()
    _connection("entrepot_join_v1", f"sqlite:///{path}")
    sid = _session()
    client.post(f"/api/files/{sid}/sources/external_db", json={
        "name": "clients", "connection": "entrepot_join_v1",
        "query": "SELECT nom, pays FROM clients", "params": {}})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM"], "fields": FIELDS,
        "sql_computed": [{"name": "PAYS", "mode": "replace",
                          "expression": "SELECT self._row_id, clients.pays "
                                       "FROM self LEFT JOIN clients ON self.NOM = clients.nom"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    pays_col = body["columns"].index("pays")
    assert body["data"][0][pays_col] == "FR"


def test_a_connection_of_the_wrong_kind_is_refused():
    client.post("/api/variables", json={"name": "pas_une_base_attach_v1", "value": "1",
                                        "scope": "global", "kind": "value"})
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/external_db", json={
        "name": "x", "connection": "pas_une_base_attach_v1", "query": "SELECT 1", "params": {}})
    assert r.status_code == 404


def test_a_query_beyond_the_row_cap_is_refused():
    path = _external_db()
    _connection("entrepot_cap_v1", f"sqlite:///{path}")
    sid = _session()
    import app.main as main_module
    old_cap = main_module.MAX_SOURCE_ROWS
    main_module.MAX_SOURCE_ROWS = 1
    try:
        r = client.post(f"/api/files/{sid}/sources/external_db", json={
            "name": "clients", "connection": "entrepot_cap_v1",
            "query": "SELECT * FROM clients", "params": {}})
        assert r.status_code == 413
    finally:
        main_module.MAX_SOURCE_ROWS = old_cap
