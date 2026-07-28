"""
Attaching an extra frame to a session — a table, or an uploaded file — for
cross-source SQL. The source lives next to the session it was attached to;
it is never a second session, and it disappears with it.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "SIRET;CIVILITE;MONTANT\n12345678901234;M;10\n99999999999999;F;20\n"
FIELDS = {"SIRET": {"name": ["SIRET"], "type": "string", "identifiant": True},
          "CIVILITE": {"name": ["CIVILITE"], "type": "string"},
          "MONTANT": {"name": ["MONTANT"], "type": "string"}}
COLS = ["SIRET", "CIVILITE", "MONTANT"]


def _upload(csv: str = CSV):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _run(sid: str):
    r = client.post(f"/api/files/{sid}/process", json={"visible_cols": COLS, "fields": FIELDS})
    assert r.status_code == 200, r.text
    return r.json()


def _write(sid: str, name: str) -> str:
    r = client.post(f"/api/files/{sid}/datasets/write",
                    json={"mode": "replace", "policy": "reject", "columns": COLS,
                          "name": name, "key_fields": ["SIRET"]})
    assert r.status_code == 200, r.text
    return r.json()["dataset"]["id"]


def test_attach_a_dataset_source_and_list_it():
    ds_sid = _upload()
    _run(ds_sid)
    ds_id = _write(ds_sid, "source_referentiel_v1")

    working_sid = _upload()
    r = client.post(f"/api/files/{working_sid}/sources/dataset",
                    json={"name": "ref", "dataset_id": ds_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "ref" and body["row_count"] == 2
    assert set(body["columns"]) == set(COLS)

    listed = client.get(f"/api/files/{working_sid}/sources").json()
    assert len(listed) == 1 and listed[0]["name"] == "ref"


def test_a_source_needs_a_name():
    ds_sid = _upload()
    _run(ds_sid)
    ds_id = _write(ds_sid, "source_sans_nom_v1")
    working_sid = _upload()
    r = client.post(f"/api/files/{working_sid}/sources/dataset",
                    json={"name": "  ", "dataset_id": ds_id})
    assert r.status_code == 422


def test_detach_removes_the_source():
    ds_sid = _upload()
    _run(ds_sid)
    ds_id = _write(ds_sid, "source_detach_v1")
    working_sid = _upload()
    client.post(f"/api/files/{working_sid}/sources/dataset", json={"name": "ref", "dataset_id": ds_id})
    r = client.delete(f"/api/files/{working_sid}/sources/ref")
    assert r.status_code == 200
    assert client.get(f"/api/files/{working_sid}/sources").json() == []


def test_attach_an_uploaded_file_as_a_source():
    working_sid = _upload()
    r = client.post(f"/api/files/{working_sid}/sources/upload",
                    data={"name": "fichier_externe"},
                    files={"file": ("x.csv", io.BytesIO(b"A;B\n1;2\n"), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["A", "B"]
    assert r.json()["row_count"] == 1


def test_a_dataset_too_large_is_refused():
    ds_sid = _upload()
    _run(ds_sid)
    ds_id = _write(ds_sid, "source_grosse_v1")
    working_sid = _upload()
    import app.main as _main
    old_cap = _main.MAX_SOURCE_ROWS
    _main.MAX_SOURCE_ROWS = 1
    try:
        r = client.post(f"/api/files/{working_sid}/sources/dataset",
                        json={"name": "ref", "dataset_id": ds_id})
        assert r.status_code == 413
    finally:
        _main.MAX_SOURCE_ROWS = old_cap


def test_a_dataset_source_masks_encrypted_columns():
    import os
    os.environ.setdefault("FX_MASTER_KEY", "cle-test-sources")
    from app.services import crypto_service as cs

    ds_sid = _upload()
    _run(ds_sid)
    ds_id = _write(ds_sid, "source_chiffree_v1")

    from app.db import session_scope
    from app.db_models import DatasetRow
    wrapped = cs.new_data_key()
    with session_scope() as s:
        for row in s.query(DatasetRow).filter_by(dataset_id=ds_id).all():
            data = dict(row.data)
            data["MONTANT"] = cs.encrypt_value(str(data.get("MONTANT", "")), wrapped)
            row.data = data
        s.commit()

    working_sid = _upload()
    r = client.post(f"/api/files/{working_sid}/sources/dataset",
                    json={"name": "chiffree", "dataset_id": ds_id})
    assert r.status_code == 200, r.text

    # The response doesn't echo values — confirm through a cross-source query
    # that the attached frame itself carries the mask, not the ciphertext.
    q = client.post(f"/api/files/{working_sid}/process", json={
        "visible_cols": [], "fields": {},
        "sql_computed": [{"name": "vu", "expression":
            "SELECT self._row_id, chiffree.MONTANT AS vu FROM self, chiffree LIMIT 1"}]})
    assert q.status_code == 200, q.text
    dump = str(q.json())
    assert "enc:v1:" not in dump
    assert cs.MASK in dump
