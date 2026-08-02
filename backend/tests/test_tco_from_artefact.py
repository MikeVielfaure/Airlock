"""
Attaching a TCO artefact into a session by reference — the shared,
admin-maintained table an environment was granted read access to, not a raw
file the operator has to have lying around. Always the latest version, so an
admin's edit reaches the next session without anyone re-uploading anything.
"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TCO_V1 = "TYPE;SOURCE_VALUE;TARGET_LABEL\ngeneric_job;01;INGENIEUR\n"
TCO_V2 = "TYPE;SOURCE_VALUE;TARGET_LABEL\ngeneric_job;01;INGENIEUR_SENIOR\n"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _signup_and_login(email):
    client.post("/api/auth/signup", json={"email": email, "password": "motdepasse1"})
    return client.post("/api/auth/login", json={"email": email, "password": "motdepasse1"}).json()["token"]


def _upload(token, env):
    r = client.post(f"/api/files?env={env}", files={"file": ("d.csv", io.BytesIO(b"A\n1\n"), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"}, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


@pytest.fixture(autouse=True)
def _clean():
    """Wipe accounts before and after each test — not just isolation, but
    what keeps `/api/auth/signup` self-service: it only allows an anonymous
    first admin when no accounts exist yet, so each test needs a clean slate
    to bootstrap its own. Artefacts are NOT wiped here (unlike accounts):
    this database is shared by the whole test run, and a blanket delete
    would both fight other test files' fixtures and violate real foreign
    keys on Postgres — unique per-test names are enough to avoid collisions."""
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
def setup():
    # A fresh, unguessable suffix per test avoids name collisions on the
    # artefacts/environments this test creates (never cleaned up — see
    # `_clean` above for why).
    tag = uuid.uuid4().hex[:8]
    hq, rh, other = f"hq-{tag}", f"rh-{tag}", f"other-{tag}"

    chef = _signup_and_login(f"chef-{tag}@x.fr")
    client.post(f"/api/admin/environments/{hq}/members", json={"email": f"chef-{tag}@x.fr", "role": "admin"}, headers=_h(chef))
    client.post(f"/api/admin/environments/{rh}/members", json={"email": f"chef-{tag}@x.fr", "role": "admin"}, headers=_h(chef))
    client.post(f"/api/admin/environments/{other}/members", json={"email": f"chef-{tag}@x.fr", "role": "admin"}, headers=_h(chef))

    op_email = f"op-rh-{tag}@x.fr"
    client.post("/api/auth/signup", json={"email": op_email, "password": "motdepasse1"}, headers=_h(chef))
    client.post(f"/api/admin/environments/{rh}/members", json={"email": op_email, "role": "operator"}, headers=_h(chef))
    op_token = client.post("/api/auth/login", json={"email": op_email, "password": "motdepasse1"}).json()["token"]

    outsider_email = f"op-other-{tag}@x.fr"
    client.post("/api/auth/signup", json={"email": outsider_email, "password": "motdepasse1"}, headers=_h(chef))
    client.post(f"/api/admin/environments/{other}/members", json={"email": outsider_email, "role": "operator"}, headers=_h(chef))
    outsider_token = client.post("/api/auth/login", json={"email": outsider_email, "password": "motdepasse1"}).json()["token"]

    tco = client.post("/api/artefacts/tco",
                      json={"name": f"referentiel-emplois-{tag}", "environment": hq, "csv": TCO_V1},
                      headers=_h(chef)).json()
    client.post(f"/api/artefacts/tco/{tco['id']}/grants", json={"environment": rh}, headers=_h(chef))

    return {"chef": chef, "operator": op_token, "outsider": outsider_token,
            "tco_id": tco["id"], "hq": hq, "rh": rh, "other": other, "tag": tag}


def test_an_operator_in_the_granted_environment_can_attach_the_tco(setup):
    sid = _upload(setup["operator"], setup["rh"])
    r = client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": setup["tco_id"]},
                    headers=_h(setup["operator"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == 1
    assert "INGENIEUR" in body["labels"]
    # The session remembers which artefact it came from, so "Correspondances"
    # can extend the same shared table instead of an unrelated pinned one.
    assert body["artefact_id"] == setup["tco_id"]


def test_uploading_a_raw_tco_file_clears_the_remembered_artefact(setup):
    """Switching to a raw file means there is no longer a library artefact
    behind the session's TCO — completing a mapping must not silently target
    whatever artefact was attached before."""
    import io
    sid = _upload(setup["operator"], setup["rh"])
    client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": setup["tco_id"]},
               headers=_h(setup["operator"]))
    r = client.post(f"/api/files/{sid}/tco",
                    files={"file": ("tco.csv", io.BytesIO(TCO_V1.encode()), "text/csv")},
                    data={"delimiter": ";"}, headers=_h(setup["operator"]))
    assert r.status_code == 200, r.text
    assert r.json()["artefact_id"] is None


def test_an_operator_outside_the_grant_cannot_see_the_artefact(setup):
    sid = _upload(setup["outsider"], setup["other"])
    r = client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": setup["tco_id"]},
                    headers=_h(setup["outsider"]))
    assert r.status_code == 404


def test_editing_the_shared_tco_propagates_to_the_next_load(setup):
    """The admin adds a new version; the operator's *next* attach (a fresh
    session, or a re-attach) sees it — no re-upload needed anywhere."""
    client.post(f"/api/artefacts/tco/{setup['tco_id']}/versions",
               json={"csv": TCO_V2}, headers=_h(setup["chef"]))

    sid = _upload(setup["operator"], setup["rh"])
    r = client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": setup["tco_id"]},
                    headers=_h(setup["operator"]))
    assert r.status_code == 200, r.text
    assert r.json()["labels"] == ["INGENIEUR_SENIOR"]


def test_a_non_tco_artefact_is_refused(setup):
    cfg = client.post("/api/artefacts/config",
                      json={"name": f"cfg-pas-un-tco-{setup['tag']}", "environment": setup["hq"],
                            "yaml": "type: CSV\ndelimiter: \";\"\nFields: []\n"},
                      headers=_h(setup["chef"])).json()
    sid = _upload(setup["operator"], setup["rh"])
    r = client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": cfg["id"]},
                    headers=_h(setup["operator"]))
    assert r.status_code == 409


def test_an_unknown_artefact_id_is_404(setup):
    sid = _upload(setup["operator"], setup["rh"])
    r = client.post(f"/api/files/{sid}/tco/from-artefact", json={"artefact_id": "inconnu"},
                    headers=_h(setup["operator"]))
    assert r.status_code == 404
