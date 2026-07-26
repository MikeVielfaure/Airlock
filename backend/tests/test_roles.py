"""
Capabilities: what each role may do, stated once and enforced everywhere.

The case that drives this: an HR team must run files and complete the
correspondence table, and must not rewrite the configuration those files are
checked against. `editor` used to mean both jobs.
"""
import io
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-roles")

from app.main import app                              # noqa: E402
from app.services import permissions as perms         # noqa: E402

client = TestClient(app)
CFG = "fields:\n  A:\n    type: string\n"
TCO = "SOURCE_VALUE;TARGET_LABEL\nMR;MASCULIN\n"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (AuthSession, CryptoKey, KeyHolder, Membership,
                               RevealEvent, User, UserIdentity)
    def wipe():
        with session_scope() as s:
            for m in (RevealEvent, KeyHolder, CryptoKey, AuthSession, UserIdentity,
                      Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def team():
    """A superadmin plus one account per role, all in 'rh'."""
    client.post("/api/auth/signup", json={"email": "dg@x.fr", "password": "motdepasse1"})
    dg = client.post("/api/auth/login",
                     json={"email": "dg@x.fr", "password": "motdepasse1"}).json()["token"]
    tokens = {"dg": dg}
    for role in ("viewer", "operator", "editor", "admin"):
        mail = f"{role}@x.fr"
        client.post("/api/auth/signup",
                    json={"email": mail, "password": "motdepasse1"}, headers=_h(dg))
        client.post("/api/admin/environments/rh/members",
                    json={"email": mail, "role": role}, headers=_h(dg))
        tokens[role] = client.post("/api/auth/login",
                                   json={"email": mail, "password": "motdepasse1"}).json()["token"]
    return tokens


# ── the policy itself ────────────────────────────────────────────────
def test_the_policy_is_readable_in_one_place():
    """Answering 'what can an operator do?' must not mean grepping the code."""
    ops = set(perms.capabilities_for("operator"))
    assert "file.process" in ops and "tco.append" in ops and "flow.run" in ops
    assert "config.write" not in ops and "members.manage" not in ops

    ed = set(perms.capabilities_for("editor"))
    assert "config.write" in ed
    assert "config.delete" not in ed          # destroying shared material is admin


def test_an_unknown_capability_is_refused_not_granted():
    """A typo in a guard must fail closed."""
    assert perms.can("admin", "capacite.inexistante") is False


def test_holding_a_key_is_not_a_role_and_admin_does_not_imply_it():
    assert not any(c.startswith("keys.use") for c in perms.CAPABILITIES)
    assert "keys.create" in perms.capabilities_for("admin")


# ── the HR case ──────────────────────────────────────────────────────
def test_an_operator_runs_files_but_cannot_rewrite_the_configuration(team):
    sid = client.post("/api/files",
                      files={"file": ("f.csv", io.BytesIO(b"A\nx\n"), "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                      headers=_h(team["operator"])).json()["session_id"]
    run = client.post(f"/api/files/{sid}/process?env=rh",
                      json={"visible_cols": ["A"],
                            "fields": {"A": {"name": ["A"], "type": "string"}}},
                      headers=_h(team["operator"]))
    assert run.status_code == 200, run.text          # doing the work: allowed

    ko = client.post("/api/artefacts/config?env=rh",
                     json={"name": "regles", "yaml": CFG, "environment": "rh"},
                     headers=_h(team["operator"]))
    assert ko.status_code == 403                     # designing it: refused
    assert "rôle" in ko.json()["detail"]             # and it says which role


def test_an_operator_completes_the_correspondence_table(team):
    """Fixing an unmapped value is part of doing the work, not of designing it —
    the whole HR scenario depends on it."""
    r = client.post("/api/environments/tco/append?env=rh",
                    json={"name": "corr", "environment": "rh",
                          "rows": [{"TYPE": "T", "SOURCE_VALUE": "MME",
                                    "TARGET_LABEL": "FEMININ"}]},
                    headers=_h(team["operator"]))
    assert r.status_code == 200, r.text


def test_replacing_a_whole_correspondence_table_needs_an_editor(team):
    """Completing is operator work; swapping the table wholesale is design."""
    ko = client.post("/api/artefacts/tco?env=rh",
                     json={"name": "corr2", "csv": TCO, "environment": "rh"},
                     headers=_h(team["operator"]))
    assert ko.status_code == 403
    ok = client.post("/api/artefacts/tco?env=rh",
                     json={"name": "corr2", "csv": TCO, "environment": "rh"},
                     headers=_h(team["editor"]))
    assert ok.status_code == 201


# ── the obvious way round, closed ────────────────────────────────────
def test_an_operator_cannot_rewrite_a_config_by_appending_a_version(team):
    """Guarding creation alone would leave appending open — the same edit by
    another door."""
    made = client.post("/api/artefacts/config?env=rh",
                       json={"name": "regles", "yaml": CFG, "environment": "rh"},
                       headers=_h(team["editor"]))
    assert made.status_code == 201
    aid = made.json()["id"]

    ko = client.post(f"/api/artefacts/config/{aid}/versions?env=rh",
                     json={"yaml": "fields:\n  A:\n    type: integer\n"},
                     headers=_h(team["operator"]))
    assert ko.status_code == 403
    ok = client.post(f"/api/artefacts/config/{aid}/versions?env=rh",
                     json={"yaml": "fields:\n  A:\n    type: integer\n"},
                     headers=_h(team["editor"]))
    assert ok.status_code == 200


def test_archiving_shared_material_is_an_admin_act(team):
    # A distinct name per test: accounts are wiped between tests, artefacts are
    # not, and a collision would surface as a confusing 409.
    made = client.post("/api/artefacts/config?env=rh",
                       json={"name": "regles-archive", "yaml": CFG, "environment": "rh"},
                       headers=_h(team["editor"]))
    assert made.status_code == 201, made.text
    made = made.json()
    assert client.delete(f"/api/artefacts/config/{made['id']}?env=rh",
                         headers=_h(team["editor"])).status_code == 403
    assert client.delete(f"/api/artefacts/config/{made['id']}?env=rh",
                         headers=_h(team["admin"])).status_code == 200


# ── the ladder holds at both ends ────────────────────────────────────
def test_a_viewer_reads_but_does_not_act(team):
    assert client.get("/api/artefacts/config?env=rh",
                      headers=_h(team["viewer"])).status_code == 200
    sid = client.post("/api/files",
                      files={"file": ("f.csv", io.BytesIO(b"A\nx\n"), "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                      headers=_h(team["viewer"])).json()["session_id"]
    ko = client.post(f"/api/files/{sid}/process?env=rh",
                     json={"visible_cols": ["A"],
                           "fields": {"A": {"name": ["A"], "type": "string"}}},
                     headers=_h(team["viewer"]))
    assert ko.status_code == 403


def test_an_editor_designs_but_does_not_administer(team):
    assert client.post("/api/admin/environments/rh/members",
                       json={"email": "viewer@x.fr", "role": "admin"},
                       headers=_h(team["editor"])).status_code == 403
    assert client.post("/api/keys?env=rh", json={"name": "k1"},
                       headers=_h(team["editor"])).status_code == 403


def test_running_a_flow_is_not_designing_one(team):
    y = ("name: f\nnodes:\n  - {id: s, type: inline, config: {rows: [{a: \"1\"}]}}\n"
         "  - {id: o, type: response}\nedges: [{from: s, to: o}]\n")
    assert client.post("/api/artefacts/graph?env=rh",
                       json={"name": "flx", "yaml": y, "environment": "rh"},
                       headers=_h(team["operator"])).status_code == 403
    assert client.post("/api/graphs/run?env=rh", json={"yaml": y, "environment": "rh"},
                       headers=_h(team["operator"])).status_code == 200


def test_the_caller_can_ask_what_they_may_do(team):
    """The UI asks instead of guessing from a role name."""
    me = client.get("/api/auth/state", headers=_h(team["operator"])).json()
    # Capabilities are reported per environment, since a role is per environment.
    caps = me["user"]["capabilities"]["rh"]
    assert "file.process" in caps and "config.write" not in caps
