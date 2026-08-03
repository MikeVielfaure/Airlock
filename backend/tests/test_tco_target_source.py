"""
A TCO's TARGET_LABEL is free text by default. `target_sources` lets one TYPE
of a TCO restrict it to whatever a DuckDB query against a referenced dataset
actually returns — checked both when previewing it and when a value is
actually appended, never only offered as a UI dropdown.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import AuthSession, Membership, User, UserIdentity
    def wipe():
        with session_scope() as s:
            for m in (AuthSession, UserIdentity, Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


def _make_list_dataset(rows):
    """A small reference table: [{"type": ..., "code": ...}, ...]."""
    from app.db import session_scope
    from app import repository as repo

    with session_scope() as s:
        ds = repo.create_dataset(s, f"list-{uuid.uuid4().hex[:8]}",
                                  {"columns": ["type", "code"],
                                   "types": {"type": "string", "code": "string"}})
        repo.insert_rows(s, ds.id, [{"key_hash": None, "data": r} for r in rows])
        s.commit()
        return ds.id


def _cfg_yaml():
    return "type: CSV\ndelimiter: \";\"\nFields: []\n"


def _make_tco(name, csv, target_sources=None):
    body = {"name": name, "csv": csv}
    if target_sources is not None:
        body["target_sources"] = target_sources
    r = client.post("/api/artefacts/tco", json=body)
    assert r.status_code == 201, r.text
    return r.json()


TCO_CSV = "TYPE;SOURCE_VALUE;TARGET_LABEL\njob;comptable;COMPTABLE\n"


def test_target_source_roundtrips_and_survives_a_plain_csv_only_version():
    ds_id = _make_list_dataset([{"type": "job", "code": "COMPTABLE"},
                                {"type": "job", "code": "COMMERCIAL"}])
    sources = {"job": {"dataset_id": ds_id, "query": "SELECT code AS value FROM list WHERE type='job'"}}
    art = _make_tco(f"tco-{uuid.uuid4().hex[:8]}", TCO_CSV, target_sources=sources)

    v1 = client.get(f"/api/artefacts/tco/{art['id']}/versions/1").json()
    assert v1["body"]["target_sources"]["job"]["dataset_id"] == ds_id

    # Appending a version with only a fresh csv (no target_sources mentioned)
    # must not silently drop the constraint.
    r = client.post(f"/api/artefacts/tco/{art['id']}/versions", json={"csv": TCO_CSV})
    assert r.status_code == 200, r.text
    v2 = client.get(f"/api/artefacts/tco/{art['id']}/versions/2").json()
    assert v2["body"]["target_sources"]["job"]["dataset_id"] == ds_id

    # Explicitly passing an empty target_sources is how one clears it.
    r = client.post(f"/api/artefacts/tco/{art['id']}/versions",
                    json={"csv": TCO_CSV, "target_sources": {}})
    assert r.status_code == 200, r.text
    v3 = client.get(f"/api/artefacts/tco/{art['id']}/versions/3").json()
    assert not v3["body"].get("target_sources")


def test_resolve_target_values_route():
    ds_id = _make_list_dataset([{"type": "job", "code": "COMPTABLE"},
                                {"type": "job", "code": "COMMERCIAL"},
                                {"type": "legal_structure", "code": "SARL"}])
    r = client.post("/api/environments/tco/resolve-target-values", json={
        "dataset_id": ds_id, "query": "SELECT code AS value FROM list WHERE type='job'"})
    assert r.status_code == 200, r.text
    assert set(r.json()["values"]) == {"COMPTABLE", "COMMERCIAL"}

    # A query with no `value` column is rejected with a clear message.
    bad = client.post("/api/environments/tco/resolve-target-values", json={
        "dataset_id": ds_id, "query": "SELECT code FROM list"})
    assert bad.status_code == 422
    assert "value" in bad.json()["detail"]

    missing = client.post("/api/environments/tco/resolve-target-values", json={
        "dataset_id": "nope", "query": "SELECT code AS value FROM list"})
    assert missing.status_code == 404


def test_append_tco_enforces_the_target_source():
    ds_id = _make_list_dataset([{"type": "job", "code": "COMPTABLE"},
                                {"type": "job", "code": "COMMERCIAL"}])
    sources = {"job": {"dataset_id": ds_id, "query": "SELECT code AS value FROM list WHERE type='job'"}}
    art = _make_tco(f"tco-{uuid.uuid4().hex[:8]}", TCO_CSV, target_sources=sources)

    ok = client.post("/api/environments/tco/append", json={
        "artefact_id": art["id"],
        "rows": [{"TYPE": "job", "SOURCE_VALUE": "vendeur", "TARGET_LABEL": "COMMERCIAL"}],
    })
    assert ok.status_code == 200, ok.text

    ko = client.post("/api/environments/tco/append", json={
        "artefact_id": art["id"],
        "rows": [{"TYPE": "job", "SOURCE_VALUE": "inventeur", "TARGET_LABEL": "INVENTEUR"}],
    })
    assert ko.status_code == 409
    assert "INVENTEUR" in ko.json()["detail"]

    # A type with no target_source stays free text, unaffected.
    free = client.post("/api/environments/tco/append", json={
        "artefact_id": art["id"],
        "rows": [{"TYPE": "other", "SOURCE_VALUE": "x", "TARGET_LABEL": "n'importe quoi"}],
    })
    assert free.status_code == 200, free.text
