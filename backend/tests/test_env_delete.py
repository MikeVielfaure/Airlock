"""
Deleting an environment: migrate what's chosen, then either detach the rest
(profile-only, orphaned but untouched) or destroy it (cascade).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
CFG = "type: CSV\ndelimiter: \";\"\nFields:\n  - name: [A]\n    type: string\n"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _delete(path, body, token):
    return client.request("DELETE", path, json=body, headers=_h(token))


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (Artefact, ArtefactGrant, ArtefactVersion, AuthSession,
                               Dataset, DatasetRow, EnvironmentProfile, Flow,
                               Membership, User, UserIdentity)
    def wipe():
        with session_scope() as s:
            for m in (ArtefactGrant, ArtefactVersion, Artefact, DatasetRow, Dataset,
                     Flow, EnvironmentProfile, AuthSession, UserIdentity,
                     Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def chef():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    return client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]


def _cfg(token, name, env):
    r = client.post("/api/artefacts/config", json={
        "name": name, "yaml": CFG, "environment": env}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_default_cannot_be_deleted(chef):
    r = _delete("/api/environments/default", {"mode": "profile_only"}, chef)
    assert r.status_code == 422


def test_a_non_superadmin_cannot_delete_an_environment(chef):
    client.post("/api/admin/quick-user",
               json={"password": "motdepasse1", "email": "op@x.fr", "memberships": {"jetable": "admin"}},
               headers=_h(chef))
    op = client.post("/api/auth/login",
                     json={"email": "op@x.fr", "password": "motdepasse1"}).json()["token"]
    r = _delete("/api/environments/jetable", {"mode": "profile_only"}, op)
    assert r.status_code == 403


def test_profile_only_leaves_the_data_alone_but_orphans_the_environment(chef):
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    cid = _cfg(chef, "cfg-jetable", "jetable")

    r = _delete("/api/environments/jetable", {"mode": "profile_only"}, chef)
    assert r.status_code == 200, r.text

    # the profile is gone …
    assert client.get("/api/environments/jetable/profile").json()["config_locked"] is False
    # … but the artefact is untouched, and the environment still shows up
    assert client.get(f"/api/artefacts/config/{cid}", headers=_h(chef)).status_code == 200
    names = client.get("/api/environments").json()["environments"]
    assert "jetable" in names          # derived from its remaining data, not a profile


def test_cascade_requires_typing_the_name_to_confirm(chef):
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    r = _delete("/api/environments/jetable", {"mode": "cascade"}, chef)
    assert r.status_code == 422
    assert "confirm" in r.json()["detail"].lower() or "tapez" in r.json()["detail"].lower()


def test_cascade_deletes_what_was_not_migrated(chef):
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    cid = _cfg(chef, "cfg-jetable", "jetable")

    r = _delete("/api/environments/jetable",
               {"mode": "cascade", "confirm_name": "jetable"}, chef)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/artefacts/config/{cid}", headers=_h(chef)).status_code == 404


def test_selected_artefacts_are_migrated_before_cascade_destroys_the_rest(chef):
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    keep_id = _cfg(chef, "cfg-a-garder", "jetable")
    gone_id = _cfg(chef, "cfg-a-perdre", "jetable")

    r = _delete("/api/environments/jetable", {
        "mode": "cascade", "confirm_name": "jetable",
        "migrate_artefact_ids": [keep_id], "target_environment": "default"}, chef)
    assert r.status_code == 200, r.text

    kept = client.get(f"/api/artefacts/config/{keep_id}", headers=_h(chef)).json()
    assert kept["environment"] == "default"
    assert client.get(f"/api/artefacts/config/{gone_id}", headers=_h(chef)).status_code == 404


def test_migrating_into_a_name_already_taken_renames_instead_of_failing(chef):
    _cfg(chef, "cfg-partage", "default")            # occupies the name first
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    moving_id = _cfg(chef, "cfg-partage", "jetable")  # same name, different environment

    r = _delete("/api/environments/jetable", {
        "mode": "profile_only", "migrate_artefact_ids": [moving_id],
        "target_environment": "default"}, chef)
    assert r.status_code == 200, r.text

    moved = client.get(f"/api/artefacts/config/{moving_id}", headers=_h(chef)).json()
    assert moved["environment"] == "default"
    assert moved["name"] == "cfg-partage-migré"       # renamed, not rejected


def test_cascade_refuses_to_delete_an_artefact_still_used_by_an_active_flow(chef):
    client.post("/api/environments", json={"name": "jetable", "template": "complet"})
    cid = _cfg(chef, "cfg-utilisee", "jetable")
    client.post("/api/flows", json={
        "name": "flux-actif", "config_artefact_id": cid}, headers=_h(chef))

    r = _delete("/api/environments/jetable",
               {"mode": "cascade", "confirm_name": "jetable"}, chef)
    assert r.status_code == 409
    assert "cfg-utilisee" in r.json()["detail"]
