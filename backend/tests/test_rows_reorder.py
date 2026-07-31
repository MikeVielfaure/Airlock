"""
Row order for a session: reordering (drag-and-drop), the new row indices a
paste needs to fill cells right after creating them, and exporting the
current object in pivot shape.
"""
import io
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "SIRET;CIVILITE;MONTANT\n11111111111111;M;10\n22222222222222;F;20\n33333333333333;M;30\n"
FIELDS = {"SIRET": {"name": ["SIRET"], "type": "string", "identifiant": True},
          "CIVILITE": {"name": ["CIVILITE"], "type": "string"},
          "MONTANT": {"name": ["MONTANT"], "type": "string"}}
COLS = ["SIRET", "CIVILITE", "MONTANT"]


def _upload(csv: str = CSV):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _preview_index(sid: str):
    return client.get(f"/api/files/{sid}/preview").json()["index"]


# ── reorder ─────────────────────────────────────────────────────────────
def test_moving_a_row_to_the_start():
    sid = _upload()
    idx = _preview_index(sid)          # [0, 1, 2] initially
    r = client.post(f"/api/files/{sid}/rows/reorder", json={"index": idx[2], "after": None})
    assert r.status_code == 200, r.text
    assert r.json()["index"][0] == idx[2]
    assert r.json()["index"][1:] == idx[:2]


def test_moving_a_row_between_two_others():
    sid = _upload()
    idx = _preview_index(sid)
    r = client.post(f"/api/files/{sid}/rows/reorder", json={"index": idx[0], "after": idx[1]})
    assert r.status_code == 200, r.text
    assert r.json()["index"] == [idx[1], idx[0], idx[2]]


def test_reordering_an_unknown_row_is_refused():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/reorder", json={"index": 999999, "after": None})
    assert r.status_code == 404


def test_reordering_after_an_unknown_row_is_refused():
    sid = _upload()
    idx = _preview_index(sid)
    r = client.post(f"/api/files/{sid}/rows/reorder", json={"index": idx[0], "after": 999999})
    assert r.status_code == 404


def test_the_new_order_survives_export_and_a_run():
    sid = _upload()
    idx = _preview_index(sid)
    client.post(f"/api/files/{sid}/rows/reorder", json={"index": idx[2], "after": None})

    exp = client.get(f"/api/files/{sid}/export", params={"fmt": "csv"})
    assert exp.status_code == 200
    lines = [l for l in exp.text.splitlines() if l.strip()]
    assert lines[1].startswith("33333333333333")   # the moved row now leads

    run = client.post(f"/api/files/{sid}/process", json={"visible_cols": COLS, "fields": FIELDS})
    assert run.json()["data"][0][0] == "33333333333333"


# ── new_indices from rows/add ────────────────────────────────────────────
def test_rows_add_returns_the_new_indices_in_order():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/add", json={"count": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["new_indices"]) == 3
    assert len(set(body["new_indices"])) == 3          # all distinct
    # they are exactly the rows a caller would want to fill next
    prev = _preview_index(sid)
    assert set(body["new_indices"]) <= set(prev)


def test_duplicating_a_row_returns_one_new_index():
    sid = _upload()
    idx = _preview_index(sid)
    r = client.post(f"/api/files/{sid}/rows/add", json={"copy_from": idx[0]})
    assert r.status_code == 200, r.text
    assert len(r.json()["new_indices"]) == 1


# ── pivot export ──────────────────────────────────────────────────────────
def test_pivot_export_folds_every_row_into_one_headless_document():
    sid = _upload()
    r = client.get(f"/api/files/{sid}/export", params={"fmt": "pivot", "filename": "objet"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="objet.json"' in r.headers["content-disposition"]
    records = json.loads(r.text)
    assert len(records) == 1                # no head grouping: one document
    doc = records[0]
    assert doc["head"] == {}
    assert len(doc["items"]) == 3
    assert {"SIRET", "CIVILITE", "MONTANT"} <= set(doc["items"][0].keys())
    values = {it["SIRET"] for it in doc["items"]}
    assert values == {"11111111111111", "22222222222222", "33333333333333"}


def test_pivot_export_respects_filters_like_the_other_formats():
    sid = _upload()
    filters = json.dumps({"CIVILITE": "=M"})
    r = client.get(f"/api/files/{sid}/export", params={"fmt": "pivot", "filters": filters})
    assert r.status_code == 200, r.text
    doc = json.loads(r.text)[0]
    assert len(doc["items"]) == 2
    assert all(it["CIVILITE"] == "M" for it in doc["items"])
