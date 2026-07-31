"""
Sharing a connection point with an environment must never mean the right to
create one: a global variable takes real cross-environment authority
(superadmin, not just admin of whichever environment a query parameter
happens to name), and an environment-scoped one takes admin of THAT
environment specifically — the one the request actually declares.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (AuthSession, CryptoKey, KeyHolder, Membership,
                               RevealEvent, User, UserIdentity, Variable,
                               VariableRestriction)
    def wipe():
        with session_scope() as s:
            for m in (VariableRestriction, Variable, RevealEvent, KeyHolder, CryptoKey,
                      AuthSession, UserIdentity, Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def tokens():
    """A true superadmin (dg), and an admin of 'rh' only (rh_admin)."""
    client.post("/api/auth/signup", json={"email": "dg@x.fr", "password": "motdepasse1"})
    dg = client.post("/api/auth/login",
                     json={"email": "dg@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/auth/signup", json={"email": "rh@x.fr", "password": "motdepasse1"},
               headers=_h(dg))
    client.post("/api/admin/environments/rh/members",
               json={"email": "rh@x.fr", "role": "admin"}, headers=_h(dg))
    rh = client.post("/api/auth/login",
                     json={"email": "rh@x.fr", "password": "motdepasse1"}).json()["token"]
    return {"dg": dg, "rh_admin": rh}


def test_an_environment_admin_cannot_create_a_global_variable(tokens):
    r = client.post("/api/variables?env=rh",
                    json={"name": "smtp_v1", "value": "1", "scope": "global"},
                    headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403


def test_a_superadmin_can_create_a_global_variable(tokens):
    r = client.post("/api/variables",
                    json={"name": "smtp_v1", "value": "1", "scope": "global"},
                    headers=_h(tokens["dg"]))
    assert r.status_code == 200, r.text


def test_an_environment_admin_can_create_a_variable_for_their_own_environment(tokens):
    r = client.post("/api/variables",
                    json={"name": "seuil_v1", "value": "1", "scope": "environment",
                          "environment": "rh"}, headers=_h(tokens["rh_admin"]))
    assert r.status_code == 200, r.text


def test_an_environment_admin_cannot_create_a_variable_for_another_environment(tokens):
    r = client.post("/api/variables",
                    json={"name": "seuil_v1", "value": "1", "scope": "environment",
                          "environment": "marketing"}, headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403


def test_the_query_param_environment_cannot_be_used_to_borrow_authority(tokens):
    """The old loophole: authorizing against `?env=rh` (where the caller
    really is admin) while the request body targets an environment they
    have no rights over must not work anymore — the two are now the same
    thing, not two independent inputs."""
    r = client.post("/api/variables?env=rh",
                    json={"name": "seuil_v2", "value": "1", "scope": "environment",
                          "environment": "marketing"}, headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403


def test_deleting_a_global_variable_needs_superadmin(tokens):
    created = client.post("/api/variables",
                          json={"name": "smtp_del_v1", "value": "1", "scope": "global"},
                          headers=_h(tokens["dg"])).json()
    r = client.delete(f"/api/variables/{created['id']}", headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403
    r2 = client.delete(f"/api/variables/{created['id']}", headers=_h(tokens["dg"]))
    assert r2.status_code == 200


def test_an_environment_admin_can_still_delete_their_own_environment_variable(tokens):
    created = client.post("/api/variables",
                          json={"name": "seuil_del_v1", "value": "1", "scope": "environment",
                                "environment": "rh"}, headers=_h(tokens["rh_admin"])).json()
    r = client.delete(f"/api/variables/{created['id']}", headers=_h(tokens["rh_admin"]))
    assert r.status_code == 200


def test_restricting_a_global_needs_superadmin(tokens):
    created = client.post("/api/variables",
                          json={"name": "smtp_restrict_v1", "value": "1", "scope": "global"},
                          headers=_h(tokens["dg"])).json()
    r = client.post(f"/api/variables/{created['id']}/restrictions",
                    json={"environment": "rh"}, headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403
    r2 = client.post(f"/api/variables/{created['id']}/restrictions",
                     json={"environment": "rh"}, headers=_h(tokens["dg"]))
    assert r2.status_code == 200


def test_removing_a_restriction_needs_superadmin(tokens):
    created = client.post("/api/variables",
                          json={"name": "smtp_unrestrict_v1", "value": "1", "scope": "global"},
                          headers=_h(tokens["dg"])).json()
    client.post(f"/api/variables/{created['id']}/restrictions",
               json={"environment": "rh"}, headers=_h(tokens["dg"]))
    r = client.delete(f"/api/variables/{created['id']}/restrictions/rh",
                      headers=_h(tokens["rh_admin"]))
    assert r.status_code == 403
    r2 = client.delete(f"/api/variables/{created['id']}/restrictions/rh",
                       headers=_h(tokens["dg"]))
    assert r2.status_code == 200
