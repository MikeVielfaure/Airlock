"""
Sharing an artefact with another environment, without moving ownership: the
owning environment still holds the only pen (versions), a grant only widens
who may read.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
CFG = "type: CSV\ndelimiter: \";\"\nFields:\n  - name: [A]\n    type: string\n"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (Artefact, ArtefactGrant, ArtefactVersion, AuthSession,
                               Membership, User, UserIdentity)
    def wipe():
        with session_scope() as s:
            for m in (ArtefactGrant, ArtefactVersion, Artefact, AuthSession,
                     UserIdentity, Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def team():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/quick-user",
               json={"email": "emma@x.fr", "memberships": {"source": "admin"}},
               headers=_h(chef))
    emma = client.post("/api/auth/login",
                       json={"email": "emma@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/quick-user",
               json={"email": "paul@x.fr", "memberships": {"target": "viewer"}},
               headers=_h(chef))
    paul = client.post("/api/auth/login",
                       json={"email": "paul@x.fr", "password": "motdepasse1"}).json()["token"]
    return {"chef": chef, "emma": emma, "paul": paul}


def _cfg(token, name="cfg-share", env="source"):
    r = client.post("/api/artefacts/config", json={
        "name": name, "yaml": CFG, "environment": env}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_an_ungranted_artefact_is_invisible_to_another_environment(team):
    _cfg(team["emma"])
    seen = [a["name"] for a in
            client.get("/api/artefacts/config?env=target", headers=_h(team["paul"])).json()]
    assert "cfg-share" not in seen


def test_the_owning_environments_admin_can_share_it(team):
    cid = _cfg(team["emma"])
    r = client.post(f"/api/artefacts/config/{cid}/grants",
                    json={"environment": "target"}, headers=_h(team["emma"]))
    assert r.status_code == 200, r.text
    assert r.json()["grants"] == [{"environment": "target", "permission": "read"}]

    seen = [a["name"] for a in
            client.get("/api/artefacts/config?env=target", headers=_h(team["paul"])).json()]
    assert "cfg-share" in seen
    # ownership itself never moved
    mine = [a["name"] for a in
            client.get("/api/artefacts/config?env=source", headers=_h(team["emma"])).json()]
    assert "cfg-share" in mine


def test_the_beneficiary_environment_cannot_grant_itself_access(team):
    """The administration of the owning side decides — not the receiving side."""
    cid = _cfg(team["emma"])
    r = client.post(f"/api/artefacts/config/{cid}/grants",
                    json={"environment": "target"}, headers=_h(team["paul"]))
    assert r.status_code == 403


def test_an_outsider_cannot_read_an_artefact_by_guessing_its_id(team):
    """The detail and version routes used to have no access check at all —
    knowing the id was enough. A member of neither the owning environment nor
    any granted one must be refused, and not merely told 'no'."""
    cid = _cfg(team["emma"])
    client.post("/api/admin/quick-user",
               json={"email": "dehors@x.fr", "memberships": {"ailleurs": "viewer"}},
               headers=_h(team["chef"]))
    dehors = client.post("/api/auth/login",
                         json={"email": "dehors@x.fr", "password": "motdepasse1"}).json()["token"]

    r = client.get(f"/api/artefacts/config/{cid}", headers=_h(dehors))
    assert r.status_code == 404
    rv = client.get(f"/api/artefacts/config/{cid}/versions/1", headers=_h(dehors))
    assert rv.status_code == 404
    ry = client.get(f"/api/artefacts/config/{cid}/versions/1/yaml", headers=_h(dehors))
    assert ry.status_code == 404

    # a member of the owning environment always could, and still can
    assert client.get(f"/api/artefacts/config/{cid}", headers=_h(team["emma"])).status_code == 200


def test_a_grant_unlocks_the_detail_and_version_routes_too(team):
    cid = _cfg(team["emma"])
    client.post(f"/api/artefacts/config/{cid}/grants",
               json={"environment": "target"}, headers=_h(team["emma"]))
    assert client.get(f"/api/artefacts/config/{cid}", headers=_h(team["paul"])).status_code == 200
    assert client.get(f"/api/artefacts/config/{cid}/versions/1",
                      headers=_h(team["paul"])).status_code == 200


def test_a_grant_can_be_revoked(team):
    cid = _cfg(team["emma"])
    client.post(f"/api/artefacts/config/{cid}/grants",
               json={"environment": "target"}, headers=_h(team["emma"]))
    client.delete(f"/api/artefacts/config/{cid}/grants/target", headers=_h(team["emma"]))
    seen = [a["name"] for a in
            client.get("/api/artefacts/config?env=target", headers=_h(team["paul"])).json()]
    assert "cfg-share" not in seen
