"""
`/api/variables/available` feeds pickers outside the référentiel itself — the
variable list in a calculated column, the connection select when attaching a
BDD externe / API source. Two rules matter here: a secret never appears (it
exists to connect, never to be read back into a value), and the capability
gate (`variables.read`, min "editor") is real — this route is the first thing
that actually enforces it.
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
    client.post("/api/auth/signup", json={"email": "dg@x.fr", "password": "motdepasse1"})
    dg = client.post("/api/auth/login",
                     json={"email": "dg@x.fr", "password": "motdepasse1"}).json()["token"]
    for email, role in (("editeur@x.fr", "editor"), ("operateur@x.fr", "operator")):
        client.post("/api/auth/signup", json={"email": email, "password": "motdepasse1"},
                   headers=_h(dg))
        client.post("/api/admin/environments/rh/members",
                   json={"email": email, "role": role}, headers=_h(dg))
    editor = client.post("/api/auth/login",
                         json={"email": "editeur@x.fr", "password": "motdepasse1"}).json()["token"]
    operator = client.post("/api/auth/login",
                           json={"email": "operateur@x.fr", "password": "motdepasse1"}).json()["token"]
    return {"dg": dg, "editor": editor, "operator": operator}


def test_a_secret_variable_is_excluded_outright(tokens):
    client.post("/api/variables", json={"name": "smtp_secret_v1", "value": "hunter2",
                                        "scope": "environment", "environment": "rh",
                                        "kind": "value", "secret": True}, headers=_h(tokens["dg"]))
    client.post("/api/variables", json={"name": "societe_v1", "value": "italie",
                                        "scope": "environment", "environment": "rh",
                                        "kind": "value"}, headers=_h(tokens["dg"]))
    r = client.get("/api/variables/available?env=rh", headers=_h(tokens["editor"]))
    assert r.status_code == 200, r.text
    names = [v["name"] for v in r.json()]
    assert "societe_v1" in names
    assert "smtp_secret_v1" not in names


def test_kind_filter_narrows_the_list(tokens):
    client.post("/api/variables", json={"name": "db_v1", "value": '{"url": "sqlite:///x"}',
                                        "scope": "environment", "environment": "rh",
                                        "kind": "external_db"}, headers=_h(tokens["dg"]))
    client.post("/api/variables", json={"name": "value_v1", "value": "x",
                                        "scope": "environment", "environment": "rh",
                                        "kind": "value"}, headers=_h(tokens["dg"]))
    r = client.get("/api/variables/available?env=rh&kind=external_db", headers=_h(tokens["editor"]))
    assert r.status_code == 200, r.text
    names = [v["name"] for v in r.json()]
    assert names == ["db_v1"]


def test_the_resolved_value_is_included_for_a_non_secret_variable(tokens):
    client.post("/api/variables", json={"name": "api_v1",
                                        "value": '{"base_url": "https://x", "token": "t"}',
                                        "scope": "environment", "environment": "rh",
                                        "kind": "api"}, headers=_h(tokens["dg"]))
    r = client.get("/api/variables/available?env=rh&kind=api", headers=_h(tokens["editor"]))
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["kind"] == "api"
    assert "base_url" in row["value"]


def test_an_operator_is_refused_the_capability(tokens):
    r = client.get("/api/variables/available?env=rh", headers=_h(tokens["operator"]))
    assert r.status_code == 403


def test_an_editor_is_allowed(tokens):
    r = client.get("/api/variables/available?env=rh", headers=_h(tokens["editor"]))
    assert r.status_code == 200
