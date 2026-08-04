"""
Comparing this session against an attached source on a key — added/removed/
changed rows. Unlike the [source.champ] lookup's key, this one may be
composite (a report, not a per-row expression), and a key that isn't unique
on either side is refused rather than guessed — same fail-closed stance.
"""
import io
import os

os.environ.setdefault("FX_MASTER_KEY", "cle-maitresse-de-test-pour-la-suite")

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_users_and_keys():
    """Confidential-column tests need a real account + key; wipe both before
    and after so they never depend on — or leak into — another test file's
    state, matching test_crypto.py's own established pattern."""
    from app.db import session_scope
    from app.db_models import CryptoKey, KeyHolder, RevealEvent, User
    def wipe():
        with session_scope() as s:
            for m in (RevealEvent, KeyHolder, CryptoKey, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


def _mk_dataset_with_rows(rows, name="ds-diff"):
    from app.db import session_scope
    from app import repository as repo
    import uuid
    with session_scope() as s:
        ds = repo.create_dataset(s, f"{name}-{uuid.uuid4().hex[:8]}",
                                 {"columns": list(rows[0].keys()), "types": {}})
        repo.insert_rows(s, ds.id, [{"key_hash": None, "data": r} for r in rows])
        s.commit()
        return ds.id


def _session(csv: str = "code;nom;ville\n1;Dupont;Paris\n2;Martin;Lyon\n3;Bernard;Nice\n"):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _attach(sid, name, rows):
    ds_id = _mk_dataset_with_rows(rows)
    r = client.post(f"/api/files/{sid}/sources/dataset", json={"name": name, "dataset_id": ds_id})
    assert r.status_code == 200, r.text


def test_diff_unknown_source_is_404():
    sid = _session()
    r = client.post(f"/api/files/{sid}/sources/nope/diff", json={"keys": ["code"]})
    assert r.status_code == 404


def test_diff_missing_key_column_is_422():
    sid = _session()
    _attach(sid, "ref", [{"code": "1", "nom": "Dupont", "ville": "Paris"}])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["does_not_exist"]})
    assert r.status_code == 422


def test_diff_no_keys_is_422():
    sid = _session()
    _attach(sid, "ref", [{"code": "1", "nom": "Dupont", "ville": "Paris"}])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": []})
    assert r.status_code == 422


def test_diff_detects_added_removed_and_changed_rows():
    sid = _session()
    # code=1 identical, code=2 changed (ville differs), code=3 removed
    # (only in session), code=4 added (only in source)
    _attach(sid, "ref", [
        {"code": "1", "nom": "Dupont", "ville": "Paris"},
        {"code": "2", "nom": "Martin", "ville": "Marseille"},
        {"code": "4", "nom": "Petit", "ville": "Metz"},
    ])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["code"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 1 and body["removed"] == 1
    assert body["changed"] == 1 and body["identical"] == 1
    statuses = {s["status"] for s in body["sample"]}
    assert statuses == {"added", "removed", "changed"}
    changed_row = next(s for s in body["sample"] if s["status"] == "changed")
    assert changed_row["key"] == {"code": "2"}
    assert changed_row["changes"]["ville"] == {"was": "Lyon", "now": "Marseille"}


def test_diff_composite_key():
    sid = _session("region;code;valeur\nEST;1;10\nEST;2;20\nOUEST;1;30\n")
    _attach(sid, "ref", [
        {"region": "EST", "code": "1", "valeur": "10"},
        {"region": "EST", "code": "2", "valeur": "99"},
    ])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["region", "code"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"] == 1   # OUEST/1 only in the session
    assert body["changed"] == 1  # EST/2 differs
    assert body["identical"] == 1


def test_diff_ambiguous_key_on_source_side_is_422():
    sid = _session()
    _attach(sid, "ref", [
        {"code": "1", "nom": "Dupont", "ville": "Paris"},
        {"code": "1", "nom": "Autre", "ville": "Nice"},
    ])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["code"]})
    assert r.status_code == 422
    assert "unique" in r.json()["detail"]


def test_diff_ambiguous_key_on_session_side_is_422():
    sid = _session("code;nom\n1;A\n1;B\n")
    _attach(sid, "ref", [{"code": "1", "nom": "A"}])
    r = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["code"]})
    assert r.status_code == 422
    assert "unique" in r.json()["detail"]


# ── a confidential column must never leak through a diff ────────────────
# Found live: masking only the session's own side (not the attached source's
# too) meant the source's real value leaked through "now", and — since a
# masked string never equals a real one — the column then falsely reported
# as "changed" on every single row, even ones that were actually identical.

def test_diff_excludes_sensitive_columns_never_leaks_and_never_false_flags():
    client.post("/api/auth/signup", json={"email": "diffkey@x.fr", "password": "motdepasse1"})
    token = client.post("/api/auth/login",
                        json={"email": "diffkey@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/keys", json={"name": "paie"}, headers=H).status_code == 200

    csv = "id;nom;salaire\n1;Dupont;50000\n2;Martin;60000\n"
    sid = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                      headers=H).json()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["id", "nom", "salaire"],
        "fields": {
            "id": {"name": ["id"], "type": "string"},
            "nom": {"name": ["nom"], "type": "string"},
            "salaire": {"name": ["salaire"], "type": "string", "sensitive": "paie"},
        }}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["data"][0][2] == "•••••"          # masked in the grid already

    # source disagrees on salaire for both rows, but agrees on nom — a
    # pre-fix run reported both as "changed" (mask vs. real never match)
    # and leaked the source's real salary in "now".
    ds_id = _mk_dataset_with_rows([
        {"id": "1", "nom": "Dupont", "salaire": "999999"},
        {"id": "2", "nom": "Martin", "salaire": "111111"},
    ], "ds-sensitive")
    assert client.post(f"/api/files/{sid}/sources/dataset", json={"name": "ref", "dataset_id": ds_id},
                       headers=H).status_code == 200

    d = client.post(f"/api/files/{sid}/sources/ref/diff", json={"keys": ["id"]}, headers=H)
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["columns_excluded_sensitive"] == ["salaire"]
    assert "salaire" not in body["columns_compared"]
    assert body["changed"] == 0 and body["identical"] == 2
    assert "999999" not in d.text and "111111" not in d.text and "50000" not in d.text
