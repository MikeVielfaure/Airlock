"""
End-to-end tests for the persistence layer: artefact library (configs,
computed, TCO) with immutable versioning, flows (pin vs track-latest),
persisted runs with frozen version ids, and the stored-export download.
"""

import io

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import MIGRATION_PATH

client = TestClient(app)

CONFIG_YAML = """
type: CSV
delimiter: ";"
Fields:
  - name: [SIRET]
    type: string
    regex: "^\\\\d{14}$"
    nullable: false
    identifiant: true
  - name: [CIVILITE]
    type: string
    tco_mapping: MASCULIN
"""

CONFIG_YAML_V2 = CONFIG_YAML.replace('regex: "^\\\\d{14}$"', 'regex: "^\\\\d{9,14}$"')

TCO_CSV = "SOURCE_VALUE;TARGET_LABEL\nM;MASCULIN\nF;FEMININ\n"

GOOD_FILE = "SIRET;CIVILITE\n12345678901234;M\n"
BAD_FILE = "SIRET;CIVILITE\n999;M\n"


def _mk_config(name="conf-a", yaml=CONFIG_YAML):
    r = client.post("/api/artefacts/config", json={"name": name, "yaml": yaml})
    assert r.status_code == 201, r.text
    return r.json()


def _mk_tco(name="tco-a"):
    r = client.post("/api/artefacts/tco", json={"name": name, "csv": TCO_CSV})
    assert r.status_code == 201, r.text
    return r.json()


def _mk_computed(name="comp-a"):
    r = client.post("/api/artefacts/computed", json={
        "name": name,
        "computed": [{"name": "salutation", "expression": 'CONCAT("Bonjour ", [SIRET])'}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _run(flow_id: str, content: str, fname="in.csv"):
    return client.post(f"/api/flows/{flow_id}/run",
                       files={"file": (fname, io.BytesIO(content.encode()), "text/csv")})


# ── schema / plumbing ─────────────────────────────────────────────────

def test_schema_via_alembic():
    # conftest ran the real init path; it must have gone through migrations.
    assert MIGRATION_PATH == "alembic"


# ── artefacts: create, validate, version, archive ─────────────────────

def test_config_create_and_duplicate_conflict():
    a = _mk_config("conf-dup")
    assert a["kind"] == "config" and a["latest_version_no"] == 1
    r = client.post("/api/artefacts/config", json={"name": "conf-dup", "yaml": CONFIG_YAML})
    assert r.status_code == 409


def test_config_bad_yaml_rejected():
    r = client.post("/api/artefacts/config", json={"name": "conf-bad", "yaml": "Fields: ["})
    assert r.status_code == 422
    assert "Invalid config YAML" in r.json()["detail"]


def test_computed_invalid_expression_rejected():
    r = client.post("/api/artefacts/computed", json={
        "name": "comp-bad",
        "computed": [{"name": "x", "expression": '__import__("os")'}]})
    assert r.status_code == 422


def test_tco_invalid_csv_rejected():
    r = client.post("/api/artefacts/tco", json={"name": "tco-bad", "csv": "ONLY_ONE_COLUMN\nx\n"})
    assert r.status_code == 422


def test_versioning_appends_and_bodies_are_immutable():
    a = _mk_config("conf-ver")
    r = client.post(f"/api/artefacts/config/{a['id']}/versions",
                    json={"yaml": CONFIG_YAML_V2, "note": "regex assoupli"})
    assert r.status_code == 200
    assert r.json()["latest_version_no"] == 2
    v1 = client.get(f"/api/artefacts/config/{a['id']}/versions/1").json()
    v2 = client.get(f"/api/artefacts/config/{a['id']}/versions/2").json()
    r1 = v1["body"]["Fields"][0]["regex"]
    r2 = v2["body"]["Fields"][0]["regex"]
    assert r1 == "^\\d{14}$" and r2 == "^\\d{9,14}$"       # v1 untouched by the "edit"


def test_unknown_kind_404_and_wrong_kind_guard():
    assert client.get("/api/artefacts/nonsense").status_code == 404
    tco = _mk_tco("tco-kindguard")
    r = client.post("/api/flows", json={"name": "flow-kindguard",
                                        "config_artefact_id": tco["id"]})
    assert r.status_code == 409                              # a tco is not a config


def test_archive_hides_from_listing():
    a = _mk_config("conf-arch")
    assert client.delete(f"/api/artefacts/config/{a['id']}").status_code == 200
    names = [x["name"] for x in client.get("/api/artefacts/config").json()]
    assert "conf-arch" not in names
    names_all = [x["name"] for x in
                 client.get("/api/artefacts/config", params={"include_archived": True}).json()]
    assert "conf-arch" in names_all


# ── flows + runs ──────────────────────────────────────────────────────

def test_flow_run_persists_frozen_versions_and_export():
    conf, tco = _mk_config("conf-run"), _mk_tco("tco-run")
    comp = _mk_computed("comp-run")
    flow = client.post("/api/flows", json={
        "name": "flux-run", "config_artefact_id": conf["id"],
        "tco_artefact_id": tco["id"], "computed_artefact_id": comp["id"],
        "default_export_filename": "sortie"}).json()

    r = _run(flow["id"], GOOD_FILE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["stage"] == "done"
    run_id = body["run_id"]
    assert run_id and r.headers["X-Run-Id"] == run_id

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["ok"] is True and detail["flow_name"] == "flux-run"
    assert detail["source_name"] == "in.csv" and detail["rows_total"] == 1
    assert detail["config_version_id"] and detail["tco_version_id"] and detail["computed_version_id"]
    assert detail["has_export"] is True and detail["export_name"] == "sortie.csv"

    exp = client.get(f"/api/runs/{run_id}/export")
    assert exp.status_code == 200
    assert "salutation" in exp.text and "Bonjour 12345678901234" in exp.text


def test_flow_run_with_errors_stores_report_no_export():
    conf, tco = _mk_config("conf-err"), _mk_tco("tco-err")
    flow = client.post("/api/flows", json={
        "name": "flux-err", "config_artefact_id": conf["id"],
        "tco_artefact_id": tco["id"]}).json()
    r = _run(flow["id"], BAD_FILE)
    body = r.json()
    assert body["ok"] is False and body["stage"] == "validation"
    run = client.get(f"/api/runs/{body['run_id']}").json()
    assert run["ok"] is False and run["rows_error"] == 1
    rows = run["report"]["rows"]
    assert rows and rows[0]["id"] == "999"
    assert any("regex" in e["message"] for e in rows[0]["errors"])
    assert run["has_export"] is False
    assert client.get(f"/api/runs/{body['run_id']}/export").status_code == 404


def test_flow_tracks_latest_and_pin_freezes():
    conf, tco = _mk_config("conf-latest"), _mk_tco("tco-latest")
    tracking = client.post("/api/flows", json={
        "name": "flux-latest", "config_artefact_id": conf["id"],
        "tco_artefact_id": tco["id"]}).json()                      # version None = latest
    pinned = client.post("/api/flows", json={
        "name": "flux-pinned", "config_artefact_id": conf["id"], "config_version_no": 1,
        "tco_artefact_id": tco["id"]}).json()

    nine = "SIRET;CIVILITE\n123456789;M\n"                          # 9 digits
    assert _run(tracking["id"], nine).json()["ok"] is False         # v1 wants 14

    client.post(f"/api/artefacts/config/{conf['id']}/versions",
                json={"yaml": CONFIG_YAML_V2})                      # v2 accepts 9-14

    ok_run = _run(tracking["id"], nine).json()
    assert ok_run["ok"] is True                                     # tracking follows v2
    assert _run(pinned["id"], nine).json()["ok"] is False           # pinned stays on v1

    # The tracking run froze the v2 version id — reproducibility anchor.
    v2_id = client.get(f"/api/artefacts/config/{conf['id']}/versions/2").json()["id"]
    assert client.get(f"/api/runs/{ok_run['run_id']}").json()["config_version_id"] == v2_id


def test_flow_missing_tco_fails_at_tco_stage():
    conf = _mk_config("conf-notco")                                 # config uses tco_mapping
    flow = client.post("/api/flows", json={
        "name": "flux-notco", "config_artefact_id": conf["id"]}).json()
    body = _run(flow["id"], GOOD_FILE).json()
    assert body["ok"] is False and body["stage"] == "tco"


def test_runs_listing_filters_by_flow():
    conf, tco = _mk_config("conf-list"), _mk_tco("tco-list")
    f1 = client.post("/api/flows", json={"name": "flux-l1", "config_artefact_id": conf["id"],
                                         "tco_artefact_id": tco["id"]}).json()
    f2 = client.post("/api/flows", json={"name": "flux-l2", "config_artefact_id": conf["id"],
                                         "tco_artefact_id": tco["id"]}).json()
    _run(f1["id"], GOOD_FILE); _run(f1["id"], GOOD_FILE); _run(f2["id"], GOOD_FILE)
    l1 = client.get("/api/runs", params={"flow_id": f1["id"]}).json()
    assert len(l1) == 2 and all(r["flow_id"] == f1["id"] for r in l1)


def test_flow_patch_repoints_config():
    strict = _mk_config("conf-patch-a")
    loose = _mk_config("conf-patch-b", yaml=CONFIG_YAML_V2)
    tco = _mk_tco("tco-patch")
    flow = client.post("/api/flows", json={"name": "flux-patch",
                                           "config_artefact_id": strict["id"],
                                           "tco_artefact_id": tco["id"]}).json()
    nine = "SIRET;CIVILITE\n123456789;M\n"
    assert _run(flow["id"], nine).json()["ok"] is False
    assert client.patch(f"/api/flows/{flow['id']}",
                        json={"config_artefact_id": loose["id"]}).status_code == 200
    assert _run(flow["id"], nine).json()["ok"] is True


def test_run_on_unknown_flow_is_404():
    r = client.post("/api/flows/doesnotexist/run",
                    files={"file": ("x.csv", io.BytesIO(b"A\n1\n"), "text/csv")})
    assert r.status_code == 404


def test_config_version_rendered_back_to_yaml_roundtrips():
    a = _mk_config("conf-yaml-rt")
    y = client.get(f"/api/artefacts/config/{a['id']}/versions/1/yaml")
    assert y.status_code == 200
    text = y.json()["yaml"]
    assert "SIRET" in text and "tco_mapping" in text
    # The rendered YAML must re-import as a valid config (round-trip).
    imp = client.post("/api/config/import", json={"yaml": text, "columns": ["SIRET", "CIVILITE"]})
    assert imp.status_code == 200
    assert len(imp.json()["match"]["matched"]) == 2
