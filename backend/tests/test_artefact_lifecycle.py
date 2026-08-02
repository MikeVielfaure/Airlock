"""
Archive, restore, and — the missing piece until now — a real hard delete.
Archiving only ever hid an artefact from listings; getting rid of one for
good needed a second, explicit step, refused while anything still depends
on it (a live flow, a profile that pins it).
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


CFG_YAML = "type: CSV\ndelimiter: \";\"\nFields: []\n"


def _cfg(name, env="default"):
    return client.post("/api/artefacts/config",
                       json={"name": name, "environment": env, "yaml": CFG_YAML}).json()


def test_environment_content_hides_archived_by_default_but_can_show_them():
    tag = uuid.uuid4().hex[:8]
    env = f"env-{tag}"
    cfg = _cfg(f"cfg-{tag}", env)
    client.delete(f"/api/artefacts/config/{cfg['id']}")   # archive

    live = client.get(f"/api/environments/{env}/content").json()
    assert all(a["id"] != cfg["id"] for a in live["artefacts"])

    everything = client.get(f"/api/environments/{env}/content?include_archived=true").json()
    found = next(a for a in everything["artefacts"] if a["id"] == cfg["id"])
    assert found["archived"] is True


def test_restoring_an_artefact_makes_it_visible_again():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-restore-{tag}")
    client.delete(f"/api/artefacts/config/{cfg['id']}")
    assert cfg["id"] not in [a["id"] for a in client.get("/api/artefacts/config").json()]

    r = client.post(f"/api/artefacts/config/{cfg['id']}/restore")
    assert r.status_code == 200, r.text
    assert cfg["id"] in [a["id"] for a in client.get("/api/artefacts/config").json()]


def test_permanent_delete_is_refused_before_archiving():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-perm-{tag}")
    r = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert r.status_code == 409
    assert "archiv" in r.json()["detail"].lower()


def test_permanent_delete_removes_it_for_good():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-gone-{tag}")
    client.delete(f"/api/artefacts/config/{cfg['id']}")
    r = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert r.status_code == 200, r.text

    everything = client.get("/api/environments/default/content?include_archived=true").json()
    assert all(a["id"] != cfg["id"] for a in everything["artefacts"])
    assert client.get(f"/api/artefacts/config/{cfg['id']}").status_code == 404


def test_permanent_delete_refused_while_a_live_flow_uses_it():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-flow-{tag}")
    flow = client.post("/api/flows", json={
        "name": f"flow-{tag}", "config_artefact_id": cfg["id"]}).json()
    client.delete(f"/api/artefacts/config/{cfg['id']}")   # archive: allowed
    r = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert r.status_code == 409
    assert flow["name"] in r.json()["detail"]

    # merely archiving the flow keeps its (real, FK-enforced) reference —
    # only deleting the flow itself frees the artefact
    client.delete(f"/api/flows/{flow['id']}")
    still_blocked = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert still_blocked.status_code == 409

    client.delete(f"/api/flows/{flow['id']}/permanent")
    r2 = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert r2.status_code == 200, r2.text


def test_permanent_delete_refused_while_a_profile_pins_it():
    tag = uuid.uuid4().hex[:8]
    env = f"env-pin-{tag}"
    cfg = _cfg(f"cfg-pin-{tag}", env)
    client.post("/api/environments", json={
        "name": env, "template": "controle_simple", "config_artefact_id": cfg["id"]})
    client.delete(f"/api/artefacts/config/{cfg['id']}")
    r = client.delete(f"/api/artefacts/config/{cfg['id']}/permanent")
    assert r.status_code == 409
    assert "profil" in r.json()["detail"].lower()


# ── flows: the same archive-then-delete two-step ─────────────────────
def test_flow_permanent_delete_two_step():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-flowlife-{tag}")
    flow = client.post("/api/flows", json={
        "name": f"flow-life-{tag}", "config_artefact_id": cfg["id"]}).json()

    ko = client.delete(f"/api/flows/{flow['id']}/permanent")
    assert ko.status_code == 409

    client.delete(f"/api/flows/{flow['id']}")   # archive
    ok = client.delete(f"/api/flows/{flow['id']}/permanent")
    assert ok.status_code == 200, ok.text
    assert client.get(f"/api/flows/{flow['id']}").status_code == 404


def test_flow_restore():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-flowrestore-{tag}")
    flow = client.post("/api/flows", json={
        "name": f"flow-restore-{tag}", "config_artefact_id": cfg["id"]}).json()
    client.delete(f"/api/flows/{flow['id']}")
    r = client.post(f"/api/flows/{flow['id']}/restore")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/flows/{flow['id']}").json()["archived"] is False


# ── archiving hides a name, it does not reserve it forever ───────────
def test_archived_artefact_name_can_be_reused():
    tag = uuid.uuid4().hex[:8]
    name = f"cfg-reuse-{tag}"
    first = _cfg(name)
    client.delete(f"/api/artefacts/config/{first['id']}")   # archive

    second = _cfg(name)   # same kind, name, environment — must succeed now
    assert second["id"] != first["id"]

    live_ids = [a["id"] for a in client.get("/api/artefacts/config").json()]
    assert second["id"] in live_ids
    assert first["id"] not in live_ids


def test_flow_name_can_be_reused_after_archiving():
    tag = uuid.uuid4().hex[:8]
    cfg = _cfg(f"cfg-flowreuse-{tag}")
    name = f"flow-reuse-{tag}"
    first = client.post("/api/flows", json={
        "name": name, "config_artefact_id": cfg["id"]}).json()
    client.delete(f"/api/flows/{first['id']}")   # archive

    second = client.post("/api/flows", json={
        "name": name, "config_artefact_id": cfg["id"]}).json()
    assert second["id"] != first["id"]


def test_dataset_name_can_be_reused_after_archiving():
    from app.db import session_scope
    from app import repository as repo

    tag = uuid.uuid4().hex[:8]
    name = f"ds-reuse-{tag}"
    schema = {"columns": ["a"], "types": {"a": "string"}}
    with session_scope() as s:
        first = repo.create_dataset(s, name, schema)
        first_id = first.id
        repo.archive_dataset(s, first_id)
        s.commit()

    with session_scope() as s:
        second = repo.create_dataset(s, name, schema)
        second_id = second.id
        s.commit()
    assert second_id != first_id


def test_dataset_restore_and_permanent_delete_routes():
    from app.db import session_scope
    from app import repository as repo

    tag = uuid.uuid4().hex[:8]
    schema = {"columns": ["a"], "types": {"a": "string"}}
    with session_scope() as s:
        ds = repo.create_dataset(s, f"ds-life-{tag}", schema)
        ds_id = ds.id
        s.commit()

    ko = client.delete(f"/api/datasets/{ds_id}/permanent")
    assert ko.status_code == 409

    client.delete(f"/api/datasets/{ds_id}")   # archive
    r = client.post(f"/api/datasets/{ds_id}/restore")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/datasets/{ds_id}").json()["archived"] is False

    client.delete(f"/api/datasets/{ds_id}")   # archive again
    ok = client.delete(f"/api/datasets/{ds_id}/permanent")
    assert ok.status_code == 200, ok.text
    assert client.get(f"/api/datasets/{ds_id}").status_code == 404
