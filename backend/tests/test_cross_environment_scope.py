"""
A capability check must verify the role in the environment a resource
actually *belongs to* — never a separately-supplied `env` query parameter,
which defaults to "default" when a caller omits it. Otherwise an admin of
one environment could reach into another's artefacts, profile or tables
just by knowing their id/name, since the query param and the resource's
real owner are two different things that happened to look the same in the
common case (everyone testing so far being admin of "default").
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


@pytest.fixture
def two_envs():
    """A superadmin bootstrapping two environments, and one admin account
    per environment — each admin only in their own."""
    tag = uuid.uuid4().hex[:8]
    envA, envB = f"a-{tag}", f"b-{tag}"

    client.post("/api/auth/signup", json={"email": f"dg-{tag}@x.fr", "password": "motdepasse1"})
    dg = client.post("/api/auth/login",
                     json={"email": f"dg-{tag}@x.fr", "password": "motdepasse1"}).json()["token"]

    client.post("/api/admin/environments/" + envA + "/members",
               json={"email": f"dg-{tag}@x.fr", "role": "admin"}, headers=_h(dg))
    client.post("/api/admin/environments/" + envB + "/members",
               json={"email": f"dg-{tag}@x.fr", "role": "admin"}, headers=_h(dg))

    admin_a_email = f"admin-a-{tag}@x.fr"
    client.post("/api/auth/signup", json={"email": admin_a_email, "password": "motdepasse1"},
               headers=_h(dg))
    client.post(f"/api/admin/environments/{envA}/members",
               json={"email": admin_a_email, "role": "admin"}, headers=_h(dg))
    admin_a = client.post("/api/auth/login",
                          json={"email": admin_a_email, "password": "motdepasse1"}).json()["token"]

    return {"dg": dg, "admin_a": admin_a, "envA": envA, "envB": envB, "tag": tag}


# ── environment profile: an admin of A cannot touch B's profile ──────
def test_admin_of_one_env_cannot_edit_anothers_profile(two_envs):
    r = client.post(f"/api/environments/{two_envs['envB']}/profile",
                    json={"tco_editable": False}, headers=_h(two_envs["admin_a"]))
    assert r.status_code == 403


def test_admin_of_one_env_cannot_read_anothers_content(two_envs):
    r = client.get(f"/api/environments/{two_envs['envB']}/content",
                   headers=_h(two_envs["admin_a"]))
    assert r.status_code == 403


def test_admin_of_one_env_cannot_reset_anothers_profile(two_envs):
    r = client.delete(f"/api/environments/{two_envs['envB']}/profile",
                      headers=_h(two_envs["admin_a"]))
    assert r.status_code == 403


def test_admin_can_still_edit_their_own_profile(two_envs):
    r = client.post(f"/api/environments/{two_envs['envA']}/profile",
                    json={"tco_editable": False}, headers=_h(two_envs["admin_a"]))
    assert r.status_code == 200, r.text


# ── artefacts: archiving reaches the artefact's own environment ──────
def test_admin_of_one_env_cannot_archive_anothers_config(two_envs):
    cfg = client.post("/api/artefacts/config",
                      json={"name": f"cfg-{two_envs['tag']}", "environment": two_envs["envB"],
                            "yaml": "type: CSV\ndelimiter: \";\"\nFields: []\n"},
                      headers=_h(two_envs["dg"])).json()
    r = client.delete(f"/api/artefacts/config/{cfg['id']}", headers=_h(two_envs["admin_a"]))
    assert r.status_code == 403


# ── TCO append: extending another environment's table needs its own role ──
def test_appending_to_anothers_tco_is_refused(two_envs):
    tco = client.post("/api/artefacts/tco",
                      json={"name": f"tco-{two_envs['tag']}", "environment": two_envs["envB"],
                            "csv": "SOURCE_VALUE;TARGET_LABEL\nM;MASCULIN\n"},
                      headers=_h(two_envs["dg"])).json()
    r = client.post("/api/environments/tco/append",
                    json={"artefact_id": tco["id"],
                          "rows": [{"SOURCE_VALUE": "F", "TARGET_LABEL": "FEMININ"}]},
                    headers=_h(two_envs["admin_a"]))
    assert r.status_code == 403


def test_appending_to_ones_own_tco_still_works(two_envs):
    tco = client.post("/api/artefacts/tco",
                      json={"name": f"tco-own-{two_envs['tag']}", "environment": two_envs["envA"],
                            "csv": "SOURCE_VALUE;TARGET_LABEL\nM;MASCULIN\n"},
                      headers=_h(two_envs["admin_a"])).json()
    r = client.post("/api/environments/tco/append",
                    json={"artefact_id": tco["id"],
                          "rows": [{"SOURCE_VALUE": "F", "TARGET_LABEL": "FEMININ"}]},
                    headers=_h(two_envs["admin_a"]))
    assert r.status_code == 200, r.text
