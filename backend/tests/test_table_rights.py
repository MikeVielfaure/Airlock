"""
Tables are not interchangeable: a business reference maintained by one team and
a scratch table someone built for themselves cannot share one permission rule.
Plus: choosing which rows actually go, one by one or in bulk.
"""
import io
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-tables")

from app.main import app                     # noqa: E402

client = TestClient(app)
CSV = "SIRET;MONTANT\n11111111111111;10\n22222222222222;20\n33333333333333;30\n"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (AuthSession, Dataset, DatasetGrant, Membership,
                               User, UserIdentity)
    def wipe():
        with session_scope() as s:
            for m in (DatasetGrant, AuthSession, UserIdentity, Membership, Dataset, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def team():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    tok = {"chef": chef}
    for role in ("operator", "editor"):
        for n in (1, 2):
            mail = f"{role}{n}@x.fr"
            client.post("/api/auth/signup",
                        json={"email": mail, "password": "motdepasse1"}, headers=_h(chef))
            client.post("/api/admin/environments/default/members",
                        json={"email": mail, "role": role}, headers=_h(chef))
            tok[f"{role}{n}"] = client.post(
                "/api/auth/login",
                json={"email": mail, "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/environments/default/members",
                json={"email": "chef@x.fr", "role": "admin"}, headers=_h(chef))
    return tok


def _session(token, text=CSV):
    sid = client.post("/api/files",
                      files={"file": ("f.csv", io.BytesIO(text.encode()), "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                      headers=_h(token)).json()["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}},
        headers=_h(token))
    return sid


def _make_table(token, name, managed=False, text=CSV):
    sid = _session(token, text)
    return client.post(f"/api/files/{sid}/datasets/write", json={
        "name": name, "mode": "replace", "policy": "all", "managed": managed,
        "key_fields": ["SIRET"], "columns": ["SIRET", "MONTANT"]},
        headers=_h(token))


# ── creating is its own right ────────────────────────────────────────
def test_creating_a_table_needs_more_than_writing_into_one(team):
    ko = _make_table(team["operator1"], "table-op")
    assert ko.status_code == 403 and "Créer une table" in ko.json()["detail"]
    ok = _make_table(team["editor1"], "table-ed")
    assert ok.status_code == 200, ok.text


# ── a personal table is private by default ───────────────────────────
def test_a_personal_table_is_not_visible_to_others(team):
    _make_table(team["editor1"], "brouillon")
    mine = [d["name"] for d in client.get("/api/datasets", headers=_h(team["editor1"])).json()]
    assert "brouillon" in mine
    # Names are rarely neutral, so a table one cannot read must not even appear.
    theirs = [d["name"] for d in client.get("/api/datasets", headers=_h(team["editor2"])).json()]
    assert "brouillon" not in theirs


def test_a_business_table_is_readable_by_the_environment(team):
    _make_table(team["editor1"], "referentiel", managed=True)
    seen = client.get("/api/datasets", headers=_h(team["operator1"])).json()
    entry = [d for d in seen if d["name"] == "referentiel"][0]
    assert entry["my_permission"] == "read"       # readable, not writable


def test_the_owner_keeps_full_rights_on_their_own_table(team):
    r = _make_table(team["editor1"], "a-moi").json()
    g = client.get(f"/api/datasets/{r['dataset']['id']}/grants",
                   headers=_h(team["editor1"])).json()
    assert g["my_permission"] == "manage" and g["is_managed"] is False


# ── writing is decided by the table ──────────────────────────────────
def test_writing_into_someone_elses_table_is_refused_until_granted(team):
    made = _make_table(team["editor1"], "partagee", managed=True).json()
    dsid = made["dataset"]["id"]

    sid = _session(team["operator1"])
    ko = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "partagee", "mode": "append", "policy": "all",
        "columns": ["SIRET", "MONTANT"]}, headers=_h(team["operator1"]))
    assert ko.status_code == 403 and "droit d'écriture" in ko.json()["detail"]

    client.post(f"/api/datasets/{dsid}/grants",
                json={"email": "operator1@x.fr", "permission": "write"},
                headers=_h(team["editor1"]))
    ok = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "partagee", "mode": "append", "policy": "all",
        "columns": ["SIRET", "MONTANT"]}, headers=_h(team["operator1"]))
    assert ok.status_code == 200, ok.text


def test_a_grant_can_target_a_role_so_it_scales(team):
    made = _make_table(team["editor1"], "par-role", managed=True).json()
    client.post(f"/api/datasets/{made['dataset']['id']}/grants",
                json={"role": "operator", "permission": "write"},
                headers=_h(team["editor1"]))
    sid = _session(team["operator2"])           # never named individually
    r = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "par-role", "mode": "append", "policy": "all",
        "columns": ["SIRET", "MONTANT"]}, headers=_h(team["operator2"]))
    assert r.status_code == 200, r.text


def test_a_grant_can_target_a_whole_other_environment(team):
    """Sharing a table with an environment, not just a person or a role in it."""
    made = _make_table(team["editor1"], "inter-env", managed=False).json()
    dsid = made["dataset"]["id"]

    client.post("/api/admin/quick-user",
                json={"email": "sat@y.fr", "memberships": {"satellite": "viewer"}},
                headers=_h(team["chef"]))
    sat = client.post("/api/auth/login",
                      json={"email": "sat@y.fr", "password": "motdepasse1"}).json()["token"]

    ko = client.post(f"/api/datasets/{dsid}/open?env=satellite", headers=_h(sat))
    assert ko.status_code == 403

    client.post(f"/api/datasets/{dsid}/grants",
                json={"environment": "satellite", "permission": "read"},
                headers=_h(team["editor1"]))
    ok = client.post(f"/api/datasets/{dsid}/open?env=satellite", headers=_h(sat))
    assert ok.status_code == 200, ok.text


def test_sharing_is_itself_a_permission(team):
    """Otherwise anyone with write could widen access indefinitely."""
    made = _make_table(team["editor1"], "verrou", managed=True).json()
    dsid = made["dataset"]["id"]
    client.post(f"/api/datasets/{dsid}/grants",
                json={"email": "operator1@x.fr", "permission": "write"},
                headers=_h(team["editor1"]))
    ko = client.post(f"/api/datasets/{dsid}/grants",
                     json={"email": "operator2@x.fr", "permission": "write"},
                     headers=_h(team["operator1"]))
    assert ko.status_code == 403


def test_an_environment_admin_can_always_manage(team):
    """A table must not outlive everyone able to administer it."""
    made = _make_table(team["editor1"], "orpheline").json()
    g = client.get(f"/api/datasets/{made['dataset']['id']}/grants",
                   headers=_h(team["chef"])).json()
    assert g["my_permission"] == "manage"


# ── choosing what actually goes ──────────────────────────────────────
def test_rows_can_be_approved_one_by_one(team):
    made = _make_table(team["editor1"], "revue").json()
    dsid = made["dataset"]["id"]

    sid = _session(team["editor1"])
    idx = client.get(f"/api/files/{sid}/preview", headers=_h(team["editor1"])).json()["index"]
    r = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "revue", "mode": "replace", "policy": "all",
        "columns": ["SIRET", "MONTANT"], "include_rows": [idx[0], idx[2]]},
        headers=_h(team["editor1"]))
    assert r.status_code == 200, r.text
    back = client.get(f"/api/datasets/{dsid}/rows", headers=_h(team["editor1"])).json()
    assert back["total_rows"] == 2
    sirets = [row[back["columns"].index("SIRET")] for row in back["data"]]
    assert sirets == ["11111111111111", "33333333333333"]


def test_a_row_can_be_excluded_instead(team):
    made = _make_table(team["editor1"], "exclusion").json()
    sid = _session(team["editor1"])
    idx = client.get(f"/api/files/{sid}/preview", headers=_h(team["editor1"])).json()["index"]
    client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "exclusion", "mode": "replace", "policy": "all",
        "columns": ["SIRET", "MONTANT"], "exclude_rows": [idx[1]]},
        headers=_h(team["editor1"]))
    back = client.get(f"/api/datasets/{made['dataset']['id']}/rows",
                      headers=_h(team["editor1"])).json()
    assert back["total_rows"] == 2
    assert "22222222222222" not in str(back["data"])


def test_approving_a_row_cannot_override_a_rule(team):
    """Selection applies after validation: ticking a bad row does not launder
    it."""
    _make_table(team["editor1"], "regles")
    bad = "SIRET;MONTANT\n11111111111111;10\n999;20\n"
    sid = client.post("/api/files",
                      files={"file": ("f.csv", io.BytesIO(bad.encode()), "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                      headers=_h(team["editor1"])).json()["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string", "regex": r"^\d{14}$"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}},
        headers=_h(team["editor1"]))
    idx = client.get(f"/api/files/{sid}/preview", headers=_h(team["editor1"])).json()["index"]
    r = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": "regles", "mode": "replace", "policy": "reject",
        "columns": ["SIRET", "MONTANT"], "include_rows": idx},
        headers=_h(team["editor1"]))
    assert r.status_code == 200, r.text
    assert r.json()["rows_written"] == 1          # the bad one stayed out
