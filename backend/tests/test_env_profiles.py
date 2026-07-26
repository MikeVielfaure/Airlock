"""
Environment profiles: deploying the same application to different audiences.
Scoping decided what an environment owns; a profile decides what it exposes.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CFG = ("type: CSV\ndelimiter: \";\"\nFields:\n"
       "  - name: [CIVILITE]\n    type: string\n    mapping: CIVILITE_NORM\n"
       "  - name: [MONTANT]\n    type: integer\n")


def _cfg(name="cfg-rh", env="rh"):
    return client.post("/api/artefacts/config",
                       json={"name": name, "yaml": CFG, "environment": env}).json()["id"]


# ── creating an environment in a few clicks ──────────────────────────
def test_templates_are_offered_with_their_modules():
    b = client.get("/api/environments/templates").json()
    keys = {t["key"] for t in b["templates"]}
    assert {"complet", "controle_simple", "consultation"} <= keys
    simple = [t for t in b["templates"] if t["key"] == "controle_simple"][0]
    assert simple["modules"] == ["data", "report", "tco"]
    assert simple["config_locked"] is True


def test_an_hr_environment_is_created_from_a_template():
    cid = _cfg("cfg-rh-1")
    r = client.post("/api/environments",
                    json={"name": "rh", "template": "controle_simple",
                          "label": "RH — contrôle", "config_artefact_id": cid})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["modules"] == ["data", "report", "tco"]      # nothing else is shown
    assert p["config_locked"] is True
    assert p["config_artefact_id"] == cid
    assert p["tco_editable"] is True                      # they may fix mappings


def test_a_locked_template_demands_the_configuration_it_pins():
    r = client.post("/api/environments",
                    json={"name": "rh-sans-config", "template": "controle_simple"})
    assert r.status_code == 422
    assert "configuration" in r.json()["detail"].lower()


def test_an_environment_without_a_profile_still_shows_everything():
    """An environment created before profiles existed must keep working."""
    p = client.get("/api/environments/jamais-configure/profile").json()
    assert p["config_locked"] is False
    assert "canvas" in p["modules"] and "data" in p["modules"]


def test_a_profile_is_editable_and_refuses_unknown_modules():
    _cfg("cfg-adv-1", "adv")
    client.post("/api/environments", json={"name": "adv", "template": "complet"})
    ok = client.post("/api/environments/adv/profile",
                     json={"modules": ["data", "report"], "tco_editable": False})
    assert ok.status_code == 200 and ok.json()["modules"] == ["data", "report"]

    ko = client.post("/api/environments/adv/profile", json={"modules": ["data", "nawak"]})
    assert ko.status_code == 422 and "nawak" in ko.json()["detail"]


def test_locking_without_a_configuration_is_refused():
    client.post("/api/environments", json={"name": "vide", "template": "complet"})
    r = client.post("/api/environments/vide/profile", json={"config_locked": True})
    assert r.status_code == 422


def test_buttons_that_call_a_flow_are_part_of_the_profile():
    gid = client.post("/api/artefacts/graph", json={
        "name": "flux-bouton", "environment": "rh",
        "yaml": ("name: bouton\nnodes:\n  - {id: s, type: inline, config: {rows: [{a: \"1\"}]}}\n"
                 "  - {id: o, type: response}\nedges: [{from: s, to: o}]\n")}).json()["id"]
    r = client.post("/api/environments/rh/profile", json={
        "actions": [{"label": "Envoyer au SIRH", "graph_id": gid,
                     "params": {"mois": "courant"}, "confirm": True}]})
    assert r.status_code == 200
    act = r.json()["actions"][0]
    assert act["label"] == "Envoyer au SIRH" and act["graph_id"] == gid

    # the button really is callable: it is an ordinary flow invocation
    call = client.post(f"/api/graphs/{gid}/call", json=act["params"])
    assert call.status_code == 200, call.text
    assert call.json()["count"] == 1


def test_a_freshly_profiled_environment_appears_in_the_environments_selector():
    """A profile with no artefact/table yet must still show up — otherwise the
    admin can never select it again right after creating it."""
    client.post("/api/environments", json={"name": "sans-donnees", "template": "complet"})
    names = client.get("/api/environments").json()["environments"]
    assert "sans-donnees" in names


def test_resetting_a_profile_leaves_the_artefacts_alone():
    _cfg("cfg-tmp", "temporaire")
    client.post("/api/environments", json={"name": "temporaire", "template": "complet"})
    client.delete("/api/environments/temporaire/profile")
    assert client.get("/api/environments/temporaire/profile").json()["config_locked"] is False
    assert any(a["name"] == "cfg-tmp"
               for a in client.get("/api/artefacts/config?env=temporaire").json())


# ── the mapping-error path: propose, then extend ─────────────────────
def test_unmapped_values_become_rows_ready_to_complete():
    r = client.post("/api/environments/tco/suggest", json={
        "uncovered": {"CIVILITE": [{"value": "MME", "count": 12},
                                   {"value": "M.", "count": 30}]},
        "field_types": {"CIVILITE": "CIVILITE_NORM"}})
    assert r.status_code == 200
    rows = r.json()["rows"]
    # The type comes from the field configuration, not from the operator: it is
    # already declared there.
    assert rows[0]["TYPE"] == "CIVILITE_NORM"
    assert rows[0]["SOURCE_VALUE"] == "M."          # most frequent first
    assert rows[0]["TARGET_LABEL"] == ""            # the one thing to fill in


def test_extending_a_correspondence_table_appends_a_version():
    tco = client.post("/api/artefacts/tco",
                      json={"name": "corr-rh", "environment": "rh",
                            "csv": "TYPE;SOURCE_VALUE;TARGET_LABEL\nCIVILITE_NORM;MR;MASCULIN\n"}).json()
    r = client.post("/api/environments/tco/append", json={
        "artefact_id": tco["id"],
        "rows": [{"TYPE": "CIVILITE_NORM", "SOURCE_VALUE": "MME", "TARGET_LABEL": "FEMININ"}]})
    assert r.status_code == 200, r.text
    assert r.json()["version_no"] == 2              # a new version, never in place
    assert r.json()["rows"] == 2


def test_a_row_without_a_target_is_refused():
    r = client.post("/api/environments/tco/append", json={
        "name": "corr-ko", "environment": "rh",
        "rows": [{"TYPE": "X", "SOURCE_VALUE": "MME", "TARGET_LABEL": ""}]})
    assert r.status_code == 422
    assert "MME" in r.json()["detail"]


def test_a_correspondence_table_can_carry_a_type_column():
    """One table then serves several fields instead of one table per field."""
    from app.services.tco_service import TcoService
    raw = ("CHAMP;SOURCE;LIBELLE\n"
           "CIVILITE_NORM;MME;FEMININ\nPAYS_NORM;FR;FRANCE\n").encode()
    df = TcoService().load_tco(raw, delimiter=";", encoding="utf-8")
    assert list(df.columns[:3]) == ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"]
    assert set(df["TYPE"]) == {"CIVILITE_NORM", "PAYS_NORM"}
