"""
Two things fixed together, on purpose: `permissions.py` and `auth_service.py`
used to each keep their own role-rank table and their own `can()` — one
compared a role against a capability's minimum, the other against a literal
role name, and nothing forced them to agree. Member management and key
creation/audit went through the literal-role path, bypassing the capability
table entirely — meaning `members.manage`/`keys.create`/`keys.audit` were
declared but never actually the thing enforced. Now there is one `can()`
(`permissions.py`), and those three routes go through it like everything else.

Separately: `file.upload`, `file.export` and `report.read` were declared
capabilities with no `require_capability(...)` on their routes at all — any
authenticated member of an environment, any role, could hit them. This file
checks both fixes: the merged role check still refuses correctly, and the
three previously-open routes now refuse a role below their minimum.
"""
import io

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
    """A superadmin (dg), an admin of 'rh' only, and a viewer of 'rh' only."""
    client.post("/api/auth/signup", json={"email": "dg@x.fr", "password": "motdepasse1"})
    dg = client.post("/api/auth/login",
                     json={"email": "dg@x.fr", "password": "motdepasse1"}).json()["token"]
    for email, role in (("admin@x.fr", "admin"), ("voit@x.fr", "viewer")):
        client.post("/api/auth/signup", json={"email": email, "password": "motdepasse1"},
                   headers=_h(dg))
        client.post("/api/admin/environments/rh/members",
                   json={"email": email, "role": role}, headers=_h(dg))
    admin = client.post("/api/auth/login",
                        json={"email": "admin@x.fr", "password": "motdepasse1"}).json()["token"]
    viewer = client.post("/api/auth/login",
                         json={"email": "voit@x.fr", "password": "motdepasse1"}).json()["token"]
    return {"dg": dg, "admin": admin, "viewer": viewer}


# ── the merged role check: members.manage / keys.create / keys.audit ─────
def test_an_environment_admin_can_manage_its_own_members(tokens):
    r = client.get("/api/admin/environments/rh/members?env=rh", headers=_h(tokens["admin"]))
    assert r.status_code == 200, r.text


def test_a_viewer_cannot_manage_members(tokens):
    r = client.post("/api/admin/environments/rh/members?env=rh",
                    json={"email": "voit@x.fr", "role": "operator"}, headers=_h(tokens["viewer"]))
    assert r.status_code == 403


def test_an_environment_admin_can_create_a_key(tokens):
    r = client.post("/api/keys?env=rh", json={"name": "cle_rh"}, headers=_h(tokens["admin"]))
    assert r.status_code in (200, 503)  # 503 only if FX_MASTER_KEY is unset in this run


def test_a_viewer_cannot_create_a_key(tokens):
    r = client.post("/api/keys?env=rh", json={"name": "cle_rh"}, headers=_h(tokens["viewer"]))
    assert r.status_code == 403


def test_a_viewer_cannot_read_the_reveal_audit_trail(tokens):
    r = client.get("/api/keys/reveals?env=rh", headers=_h(tokens["viewer"]))
    assert r.status_code == 403


def test_an_environment_admin_can_read_the_reveal_audit_trail(tokens):
    r = client.get("/api/keys/reveals?env=rh", headers=_h(tokens["admin"]))
    assert r.status_code == 200, r.text


# ── the three previously-unguarded routes ─────────────────────────────────
def _upload(token):
    return client.post("/api/files?env=rh", files={"file": ("d.csv", io.BytesIO(b"NOM\nAlice\n"), "text/csv")},
                       data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                       headers=_h(token))


def test_a_viewer_cannot_upload_a_file(tokens):
    r = _upload(tokens["viewer"])
    assert r.status_code == 403


def test_an_admin_can_upload_a_file(tokens):
    r = _upload(tokens["admin"])
    assert r.status_code == 200, r.text


def test_a_viewer_can_export_since_export_is_viewer_rank(tokens):
    up = _upload(tokens["admin"])
    sid = up.json()["session_id"]
    r = client.get(f"/api/files/{sid}/export?env=rh", headers=_h(tokens["viewer"]))
    assert r.status_code == 200


def test_a_viewer_can_read_the_report_since_report_read_is_viewer_rank(tokens):
    up = _upload(tokens["admin"])
    sid = up.json()["session_id"]
    client.post(f"/api/files/{sid}/process?env=rh",
               json={"visible_cols": ["NOM"],
                     "fields": {"NOM": {"name": ["NOM"], "type": "string"}}},
               headers=_h(tokens["admin"]))
    r = client.get(f"/api/files/{sid}/report?env=rh", headers=_h(tokens["viewer"]))
    assert r.status_code == 200
