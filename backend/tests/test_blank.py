"""
Sessions started from a schema instead of a file: the schema is the source of
truth, data comes later (or never). Two paths — explicit columns, or seeded
from a library artefact — feeding the three uses the user named: trying a config
on hand-typed rows, building a small reference by hand, seeding an empty table.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _cfg(name="seed-cfg"):
    y = ("type: CSV\ndelimiter: \";\"\nFields:\n"
         "  - name: [SIRET]\n    type: string\n    regex: \"^\\\\d{14}$\"\n"
         "    nullable: false\n    identifiant: true\n"
         "  - name: [MONTANT]\n    type: integer\n"
         "  - name: [PAYS]\n    type: string\n    mapping: pays_code\n")
    return client.post("/api/artefacts/config", json={"name": name, "yaml": y}).json()["id"]


# ── from scratch ─────────────────────────────────────────────────────
def test_a_blank_session_starts_from_columns_alone():
    r = client.post("/api/files/blank", json={"columns": ["nom", "siret", "montant"]})
    assert r.status_code == 200
    b = r.json()
    assert b["type"] == "MANUAL"
    assert b["preview"]["columns"] == ["nom", "siret", "montant"]
    assert b["preview"]["total_rows"] == 0                 # no file, no rows
    assert b["seeded_fields"] is None


def test_blank_rows_can_be_requested_up_front():
    r = client.post("/api/files/blank", json={"columns": ["a", "b"], "rows": 3})
    assert r.json()["preview"]["total_rows"] == 3


def test_columns_only_rejects_empties_and_duplicates():
    assert client.post("/api/files/blank", json={"columns": []}).status_code == 422
    assert client.post("/api/files/blank", json={"columns": ["x", "x"]}).status_code == 422


# ── the whole point: type, edit, validate — no file ──────────────────
def test_hand_typed_rows_run_through_the_pipeline():
    sid = client.post("/api/files/blank", json={"columns": ["siret", "montant"]}).json()["session_id"]
    add = client.post(f"/api/files/{sid}/rows/add", json={"count": 2}).json()
    assert add["total_rows"] == 2
    idx = client.get(f"/api/files/{sid}/preview").json()["index"]

    client.post(f"/api/files/{sid}/cells", json={"edits": [
        {"index": idx[0], "column": "siret", "value": "12345678901234"},
        {"index": idx[0], "column": "montant", "value": "10"},
        {"index": idx[1], "column": "siret", "value": "bad"},
        {"index": idx[1], "column": "montant", "value": "20"}]})

    run = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["siret", "montant"],
        "fields": {"siret": {"name": ["siret"], "type": "string", "regex": r"^\d{14}$"},
                   "montant": {"name": ["montant"], "type": "integer"}}})
    assert run.status_code == 200
    stats = run.json()["stats"]
    assert stats["total_rows"] == 2
    assert stats["rows_err"] == 1                          # the "bad" siret, flagged not blocked


# ── seeded from a config ─────────────────────────────────────────────
def test_a_session_seeds_its_columns_and_rules_from_a_config():
    aid = _cfg()
    r = client.post("/api/files/blank", json={"artefact_id": aid, "rows": 1})
    assert r.status_code == 200
    b = r.json()
    # `mapping: pays_code` means the output column is the renamed one
    assert b["preview"]["columns"] == ["SIRET", "MONTANT", "pays_code"]
    sf = b["seeded_fields"]
    assert sf["SIRET"]["regex"] == r"^\d{14}$"
    assert sf["SIRET"]["identifiant"] is True
    assert sf["MONTANT"]["type"] == "integer"


def test_a_seeded_config_validates_hand_typed_rows_immediately():
    aid = _cfg("seed-cfg-2")
    sid = client.post("/api/files/blank", json={"artefact_id": aid, "rows": 1}).json()["session_id"]
    idx = client.get(f"/api/files/{sid}/preview").json()["index"][0]
    client.post(f"/api/files/{sid}/cells", json={"edits": [
        {"index": idx, "column": "SIRET", "value": "999"}]})   # too short
    run = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT", "pays_code"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string", "regex": r"^\d{14}$",
                             "nullable": False},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"},
                   "pays_code": {"name": ["pays_code"], "type": "string"}}})
    assert run.json()["stats"]["rows_err"] == 1


# ── seeded from an EDI model ─────────────────────────────────────────
def test_a_session_seeds_columns_from_an_edi_model():
    from pathlib import Path
    model = (Path(__file__).resolve().parents[2] / "samples/edi/model_orders_d96a.yaml").read_text()
    mid = client.post("/api/artefacts/edi_model", json={"name": "seed-edi", "yaml": model}).json()["id"]
    r = client.post("/api/files/blank", json={"artefact_id": mid})
    assert r.status_code == 200
    cols = r.json()["preview"]["columns"]
    assert "numero_commande" in cols and "quantite_commandee" in cols
    assert r.json()["seeded_fields"] is None               # an EDI model brings columns, not field rules


def test_seeding_from_a_tco_artefact_is_refused_with_a_clear_message():
    tco = client.post("/api/artefacts/tco",
                      json={"name": "seed-tco", "csv": "SOURCE;TARGET_LABEL\nFR;France\n"}).json()["id"]
    r = client.post("/api/files/blank", json={"artefact_id": tco})
    assert r.status_code == 422
    assert "columns" in r.json()["detail"].lower()


def test_seeding_from_an_unknown_artefact_is_404():
    assert client.post("/api/files/blank", json={"artefact_id": "nope"}).status_code == 404


# ── the route is not an open door once accounts exist ────────────────
def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_a_viewer_cannot_start_a_blank_session():
    client.post("/api/auth/signup", json={"email": "chef-blank@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef-blank@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/auth/signup", json={"email": "voir-blank@x.fr", "password": "motdepasse1"},
               headers=_h(chef))
    client.post("/api/admin/environments/default/members",
               json={"email": "voir-blank@x.fr", "role": "viewer"}, headers=_h(chef))
    viewer = client.post("/api/auth/login",
                         json={"email": "voir-blank@x.fr", "password": "motdepasse1"}).json()["token"]

    r = client.post("/api/files/blank", json={"columns": ["a"]}, headers=_h(viewer))
    assert r.status_code == 403

    from app.db import session_scope
    from app.db_models import AuthSession, Membership, User, UserIdentity
    with session_scope() as s:
        for m in (AuthSession, UserIdentity, Membership, User):
            for row in s.query(m).all():
                s.delete(row)
        s.commit()


def test_seeding_from_an_artefact_outside_ones_environments_is_404():
    client.post("/api/auth/signup", json={"email": "chef-blank2@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef-blank2@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/environments/prive-blank/members",
               json={"email": "chef-blank2@x.fr", "role": "admin"}, headers=_h(chef))
    aid = client.post("/api/artefacts/config",
                      json={"name": "cfg-prive-blank", "environment": "prive-blank",
                            "yaml": "type: CSV\ndelimiter: \";\"\nFields:\n  - name: [A]\n    type: string\n"},
                      headers=_h(chef)).json()["id"]

    client.post("/api/auth/signup", json={"email": "dehors-blank@x.fr", "password": "motdepasse1"},
               headers=_h(chef))
    client.post("/api/admin/environments/default/members",
               json={"email": "dehors-blank@x.fr", "role": "editor"}, headers=_h(chef))
    outsider = client.post("/api/auth/login",
                           json={"email": "dehors-blank@x.fr", "password": "motdepasse1"}).json()["token"]

    r = client.post("/api/files/blank", json={"artefact_id": aid}, headers=_h(outsider))
    assert r.status_code == 404

    from app.db import session_scope
    from app.db_models import AuthSession, Membership, User, UserIdentity
    with session_scope() as s:
        for m in (AuthSession, UserIdentity, Membership, User):
            for row in s.query(m).all():
                s.delete(row)
        s.commit()
