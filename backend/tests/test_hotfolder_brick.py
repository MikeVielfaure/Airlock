"""
The hotfolder brick: pick up the oldest file waiting in a connection point's
folder and parse it through the same CSV/XLSX parser the rest of the app
uses. Triggered on demand only — nothing polls the folder in the background.
"""
import io
import json
import os
import tempfile

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _dirs():
    root = tempfile.mkdtemp(prefix="fx_hotfolder_")
    path = os.path.join(root, "in")
    archive = os.path.join(root, "ok")
    error = os.path.join(root, "ko")
    os.makedirs(path)
    return path, archive, error


def _connection(name, path, archive, error, kind="hotfolder"):
    value = json.dumps({"path": path, "archive_dir": archive, "error_dir": error})
    r = client.post("/api/variables", json={"name": name, "value": value,
                                            "scope": "global", "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def _flow(nodes_yaml):
    return client.post("/api/graphs/run", json={"yaml": nodes_yaml})


def test_a_file_found_is_parsed_into_records():
    path, archive, error = _dirs()
    _connection("depot_ok_v1", path, archive, error)
    with open(os.path.join(path, "a.csv"), "wb") as f:
        f.write("A;B\n1;2\n".encode())

    y = """
name: hf
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_ok_v1", file_type: csv, delimiter: ";"}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 1


def test_an_xlsx_file_is_parsed_too():
    path, archive, error = _dirs()
    _connection("depot_xlsx_v1", path, archive, error)
    buf = io.BytesIO()
    pd.DataFrame([{"A": 1, "B": 2}]).to_excel(buf, index=False)
    with open(os.path.join(path, "a.xlsx"), "wb") as f:
        f.write(buf.getvalue())

    y = """
name: hf-xlsx
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_xlsx_v1", file_type: xlsx}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 1


def test_the_oldest_file_is_picked_first():
    path, archive, error = _dirs()
    _connection("depot_fifo_v1", path, archive, error)
    older = os.path.join(path, "older.csv")
    newer = os.path.join(path, "newer.csv")
    with open(older, "wb") as f:
        f.write("A;B\nold;1\n".encode())
    with open(newer, "wb") as f:
        f.write("A;B\nnew;2\n".encode())
    now = os.path.getmtime(newer)
    os.utime(older, (now - 100, now - 100))

    y = """
name: hf-fifo
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_fifo_v1", file_type: csv, delimiter: ";"}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    preview = r.json()["preview"]
    row = preview["data"][0]
    assert row[preview["columns"].index("A")] == "old"


def test_no_file_and_required_by_default_blocks():
    path, archive, error = _dirs()
    _connection("depot_vide_v1", path, archive, error)
    y = """
name: hf-vide
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_vide_v1"}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 422 and "hf" in r.json()["detail"]


def test_no_file_and_not_required_yields_zero_rows():
    path, archive, error = _dirs()
    _connection("depot_optionnel_v1", path, archive, error)
    y = """
name: hf-optionnel
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_optionnel_v1", required: false}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 0


def test_a_connection_of_the_wrong_kind_is_refused():
    r = client.post("/api/variables", json={"name": "pas_un_hotfolder_v1", "value": "42",
                                            "scope": "global", "kind": "value"})
    assert r.status_code == 200, r.text
    y = """
name: hf-mauvais-kind
nodes:
  - {id: hf, type: hotfolder, config: {connection: "pas_un_hotfolder_v1"}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 422 and "hf" in r.json()["detail"]


def test_a_missing_connection_is_refused():
    y = """
name: hf-inconnue
nodes:
  - {id: hf, type: hotfolder, config: {connection: "jamais-vue-v1"}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 422 and "hf" in r.json()["detail"]
