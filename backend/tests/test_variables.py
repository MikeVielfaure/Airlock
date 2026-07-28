"""
Connection points with structure: a plain value still works exactly as
before, but a `kind` lets one hold a JSON-shaped connection (a hotfolder, an
SMTP relay) instead — one system, not a second table. Plus: an admin can
narrow a global to a handful of environments without breaking the ones that
never touch it.
"""
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _var(**kw):
    return client.post("/api/variables", json=kw)


def test_a_plain_variable_defaults_to_kind_value():
    r = _var(name="seuil_alerte_v2", value="42", scope="global")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "value"


def test_a_hotfolder_connection_needs_its_three_fields():
    incomplete = json.dumps({"path": "/data/in", "archive_dir": "/data/ok"})
    r = _var(name="depot_v2", value=incomplete, scope="global", kind="hotfolder")
    assert r.status_code == 409
    assert "error_dir" in r.json()["detail"]

    complete = json.dumps({"path": "/data/in", "archive_dir": "/data/ok",
                           "error_dir": "/data/ko"})
    r2 = _var(name="depot_v2", value=complete, scope="global", kind="hotfolder")
    assert r2.status_code == 200, r2.text
    assert json.loads(r2.json()["value"]) == json.loads(complete)


def test_an_smtp_connection_only_needs_a_host():
    r = _var(name="relais_v2", value=json.dumps({"host": "smtp.exemple.fr"}),
             scope="global", kind="smtp")
    assert r.status_code == 200, r.text
    r2 = _var(name="relais_sans_host_v2", value=json.dumps({}), scope="global", kind="smtp")
    assert r2.status_code == 409


def test_an_unknown_kind_is_refused():
    r = _var(name="mystere_v2", value="1", scope="global", kind="fax")
    assert r.status_code == 409


def test_unparsable_json_is_refused_for_a_structured_kind():
    r = _var(name="casse_v2", value="not json at all", scope="global", kind="hotfolder")
    assert r.status_code == 409


# ── restrictions ───────────────────────────────────────────────────────
def _make_global(name: str) -> str:
    return _var(name=name, value="https://global", scope="global").json()["id"]


def test_a_global_with_no_restriction_is_visible_everywhere():
    _make_global("api_base_v2")
    default = client.get("/api/variables/resolved?env=default").json()["variables"]
    rh = client.get("/api/variables/resolved?env=rh").json()["variables"]
    assert default["api_base_v2"] == rh["api_base_v2"] == "https://global"


def test_restricting_a_global_hides_it_elsewhere():
    vid = _make_global("api_restreint_v2")
    r = client.post(f"/api/variables/{vid}/restrictions", json={"environment": "rh"})
    assert r.status_code == 200, r.text

    rh = client.get("/api/variables/resolved?env=rh").json()["variables"]
    assert rh["api_restreint_v2"] == "https://global"
    default = client.get("/api/variables/resolved?env=default").json()["variables"]
    assert "api_restreint_v2" not in default


def test_removing_a_restriction_makes_it_visible_everywhere_again():
    vid = _make_global("api_temporaire_v2")
    client.post(f"/api/variables/{vid}/restrictions", json={"environment": "rh"})
    client.delete(f"/api/variables/{vid}/restrictions/rh")

    default = client.get("/api/variables/resolved?env=default").json()["variables"]
    assert "api_temporaire_v2" in default


def test_a_restriction_still_lists_administratively_outside_its_scope():
    """The listing (variables.write/read) is not the same question as the
    resolution (what a run in this environment sees) — an admin must be able
    to find and edit a restricted global from anywhere."""
    _make_global("api_liste_v2")
    names = [v["name"] for v in client.get("/api/variables?env=rh").json()]
    assert "api_liste_v2" in names


def test_only_a_global_variable_can_be_restricted():
    vid = _var(name="local_v2", value="1", scope="environment",
              environment="rh").json()["id"]
    r = client.post(f"/api/variables/{vid}/restrictions", json={"environment": "adv"})
    assert r.status_code == 409


# ── the newer connection kinds (external_db, sftp, api) ────────────────
def test_an_external_db_connection_only_needs_a_url():
    r = _var(name="entrepot_v2", value=json.dumps({"url": "sqlite:///:memory:"}),
             scope="global", kind="external_db")
    assert r.status_code == 200, r.text
    r2 = _var(name="entrepot_sans_url_v2", value=json.dumps({}), scope="global", kind="external_db")
    assert r2.status_code == 409


def test_an_sftp_connection_needs_all_five_directories():
    incomplete = json.dumps({"host": "sftp.exemple.fr", "user": "fx", "remote_dir": "/in"})
    r = _var(name="depot_distant_v2", value=incomplete, scope="global", kind="sftp")
    assert r.status_code == 409
    assert "archive_dir" in r.json()["detail"]

    complete = json.dumps({"host": "sftp.exemple.fr", "user": "fx", "remote_dir": "/in",
                           "archive_dir": "/ok", "error_dir": "/ko"})
    r2 = _var(name="depot_distant_v2", value=complete, scope="global", kind="sftp")
    assert r2.status_code == 200, r2.text


def test_an_api_connection_only_needs_a_base_url():
    r = _var(name="service_externe_v2", value=json.dumps({"base_url": "https://api.exemple.fr"}),
             scope="global", kind="api")
    assert r.status_code == 200, r.text
    r2 = _var(name="service_sans_url_v2", value=json.dumps({}), scope="global", kind="api")
    assert r2.status_code == 409


def test_resolve_variable_kinds_matches_the_resolved_value():
    _var(name="depot_kind_v2", value=json.dumps({"path": "/a", "archive_dir": "/b",
                                                 "error_dir": "/c"}),
        scope="global", kind="hotfolder")
    kinds = client.get("/api/variables/resolved-kinds?env=default").json()["kinds"]
    assert kinds["depot_kind_v2"] == "hotfolder"
