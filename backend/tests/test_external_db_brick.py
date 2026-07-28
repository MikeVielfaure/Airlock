"""
The external_db bricks: a connection point stores nothing but a SQLAlchemy
DSN, and the node's own config carries `query`/`params` — always bound,
never spliced into the SQL text. Exercised against a throwaway SQLite file
(not the app's own store) so this needs no real external server.
"""
import json
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _external_db():
    root = tempfile.mkdtemp(prefix="fx_external_db_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (id INTEGER, nom TEXT, pays TEXT)")
    con.executemany("INSERT INTO clients VALUES (?, ?, ?)",
                    [(1, "ACME", "FR"), (2, "BETA", "BE"), (3, "GAMMA", "FR")])
    con.commit()
    con.close()
    return path


def _connection(name, url, kind="external_db"):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps({"url": url}),
                                            "scope": "global", "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_bound_parameter_filters_without_string_substitution():
    path = _external_db()
    _connection("entrepot_lecture_v1", f"sqlite:///{path}")
    y = """
name: lecture-externe
nodes:
  - id: src
    type: external_db
    config:
      connection: "entrepot_lecture_v1"
      query: "SELECT * FROM clients WHERE pays = :pays"
      params: {pays: "FR"}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    prev = r.json()["preview"]
    assert prev["total_rows"] == 2
    assert "ACME" in str(prev["data"]) and "GAMMA" in str(prev["data"])
    assert "BETA" not in str(prev["data"])


def test_a_malicious_param_value_is_never_treated_as_sql():
    path = _external_db()
    _connection("entrepot_injection_v1", f"sqlite:///{path}")
    y = """
name: tentative-injection
nodes:
  - id: src
    type: external_db
    config:
      connection: "entrepot_injection_v1"
      query: "SELECT * FROM clients WHERE nom = :nom"
      params: {nom: "ACME' OR '1'='1"}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    # A bound parameter is compared literally — nothing matches this string,
    # so the injection attempt returns zero rows instead of the whole table.
    assert r.json()["preview"]["total_rows"] == 0


def test_a_connection_of_the_wrong_kind_is_refused():
    r = client.post("/api/variables", json={"name": "pas_une_base_v1", "value": "1",
                                            "scope": "global", "kind": "value"})
    assert r.status_code == 200, r.text
    y = """
name: mauvais-kind
nodes:
  - {id: src, type: external_db, config: {connection: "pas_une_base_v1", query: "SELECT 1"}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "src" in r.json()["detail"]


def test_write_inserts_then_upsert_replaces_matching_keys():
    root = tempfile.mkdtemp(prefix="fx_external_db_write_")
    path = os.path.join(root, "warehouse.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE commandes (ref TEXT, montant TEXT)")
    con.commit()
    con.close()
    _connection("entrepot_ecriture_v1", f"sqlite:///{path}")

    insert_yaml = """
name: ecrit-externe
nodes:
  - id: src
    type: inline
    config: {rows: [{ref: "R1", montant: "10"}, {ref: "R2", montant: "20"}]}
  - id: out
    type: external_db_write
    config: {connection: "entrepot_ecriture_v1", table: commandes, mode: insert}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": insert_yaml})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["written"] == 2

    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM commandes").fetchone()[0] == 2
    con.close()

    upsert_yaml = """
name: reecrit-externe
nodes:
  - id: src
    type: inline
    config: {rows: [{ref: "R1", montant: "99"}]}
  - id: out
    type: external_db_write
    config: {connection: "entrepot_ecriture_v1", table: commandes, mode: upsert, key_fields: [ref]}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": upsert_yaml})
    assert r.status_code == 200, r.text

    con = sqlite3.connect(path)
    rows = con.execute("SELECT ref, montant FROM commandes ORDER BY ref").fetchall()
    con.close()
    assert rows == [("R1", "99"), ("R2", "20")]      # R1 replaced, R2 untouched
