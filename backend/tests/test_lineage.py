"""
Lineage: which artefact an artefact was derived from.

A library of thirty configurations is unreadable without it — knowing that
"contrat-partenaireA" descends from "contrat" is the difference between a family
and a pile.
"""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-lineage")

from app.main import app                     # noqa: E402

client = TestClient(app)
CFG = "fields:\n  MATRICULE:\n    type: string\n"


def _cfg(name, derived_from="", from_version=None):
    body = {"name": name, "yaml": CFG}
    if derived_from:
        body["derived_from"] = derived_from
        if from_version:
            body["derived_from_version"] = from_version
    return client.post("/api/artefacts/config", json=body)


def _u():
    return os.urandom(3).hex()


def test_deriving_leaves_the_ancestor_untouched():
    """A different name is a different artefact: there is no path to overwriting
    the contract, even by mistake."""
    base = _cfg(f"contrat-{_u()}").json()
    var = _cfg(f"variante-{_u()}", base["id"], 1)
    assert var.status_code == 201
    assert var.json()["derived_from"] == base["id"]
    # the ancestor still has exactly one version, unchanged
    assert client.get(f"/api/artefacts/config/{base['id']}").json()["latest_version_no"] == 1


def test_the_family_is_readable_in_both_directions():
    base = _cfg(f"contrat-{_u()}").json()
    a = _cfg(f"partenaireA-{_u()}", base["id"], 1).json()
    b = _cfg(f"partenaireB-{_u()}", base["id"], 1).json()
    petit = _cfg(f"partenaireA-bis-{_u()}", a["id"], 1).json()

    down = client.get(f"/api/artefacts/config/{base['id']}/lineage").json()
    assert {c["id"] for c in down["children"]} == {a["id"], b["id"]}

    up = client.get(f"/api/artefacts/config/{petit['id']}/lineage").json()
    assert [x["id"] for x in up["ancestors"]] == [a["id"], base["id"]]


def test_the_exact_ancestor_version_is_remembered():
    """An ancestor keeps moving; "derived from v1" is a fact that stays true."""
    base = _cfg(f"contrat-{_u()}").json()
    client.post(f"/api/artefacts/config/{base['id']}/versions",
                json={"yaml": "fields:\n  MATRICULE:\n    type: integer\n"})
    var = _cfg(f"variante-{_u()}", base["id"], 1).json()

    up = client.get(f"/api/artefacts/config/{var['id']}/lineage").json()
    anc = up["ancestors"][0]
    assert anc["version_no"] == 1 and anc["latest_version_no"] == 2


def test_deriving_across_kinds_is_refused():
    """Deriving a config from a mapping would produce something nobody can read."""
    m = client.post("/api/artefacts/mapping", json={
        "name": f"map-{_u()}",
        "yaml": "name: m\nlinks:\n  - {pivot: a, source: b, scope: head}\n"}).json()
    r = _cfg(f"config-{_u()}", m["id"], 1)
    assert r.status_code == 409 and "mapping" in r.json()["detail"]


def test_an_unknown_ancestor_is_refused_at_creation():
    r = _cfg(f"orpheline-{_u()}", "inexistant", 1)
    assert r.status_code == 404


def test_a_deleted_ancestor_is_reported_rather_than_hidden():
    """"Derived from something gone" is worth knowing, so the chain says so
    instead of ending silently."""
    from app.db import session_scope
    from app.db_models import Artefact
    base = _cfg(f"contrat-{_u()}").json()
    var = _cfg(f"variante-{_u()}", base["id"], 1).json()
    with session_scope() as s:
        s.delete(s.get(Artefact, base["id"]))
        s.commit()

    up = client.get(f"/api/artefacts/config/{var['id']}/lineage").json()
    assert up["ancestors"][0]["missing"] is True


def test_lineage_survives_a_corrupted_self_reference():
    """A cycle should be impossible, but a bad row must not spin forever."""
    from app.db import session_scope
    from app.db_models import Artefact
    a = _cfg(f"boucle-{_u()}").json()
    with session_scope() as s:
        row = s.get(Artefact, a["id"])
        row.derived_from = row.id
        s.commit()
    up = client.get(f"/api/artefacts/config/{a['id']}/lineage")
    assert up.status_code == 200 and up.json()["ancestors"] == []
