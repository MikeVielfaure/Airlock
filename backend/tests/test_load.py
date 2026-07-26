"""
Loading a partner's file into a target that already exists.

The point being tested: the target's schema replaces the configuration. Nobody
writes rules — the correspondence table already says what it needs, so the file
is mapped, cleaned and checked against it.
"""
import io
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-load")

from app.main import app                     # noqa: E402

client = TestClient(app)

# A partner's export: none of these headers match the schema, and one row is bad.
PARTNER = ("Code partenaire;Désignation;Famille\n"
           "MR;MASCULIN;CIVILITE\n"
           "MME;FEMININ;CIVILITE\n"
           ";ORPHELIN;CIVILITE\n")


def _upload(text=PARTNER):
    return client.post("/api/files",
                       files={"file": ("p.csv", io.BytesIO(text.encode("utf-8")),
                                       "text/csv")},
                       data={"file_type": "CSV", "encoding": "AUTO",
                             "delimiter": ";"}).json()["session_id"]


@pytest.fixture
def tco():
    return client.post("/api/artefacts/tco",
                       json={"name": f"corr-{os.urandom(3).hex()}",
                             "csv": "TYPE;SOURCE_VALUE;TARGET_LABEL\n"
                                    "CIVILITE;MLLE;FEMININ\n"}).json()


# ── what can be loaded into ──────────────────────────────────────────
def test_targets_advertise_the_schema_they_impose(tco):
    rows = client.get("/api/targets").json()
    entry = [t for t in rows if t["id"] == tco["id"]][0]
    assert entry["kind"] == "tco"
    assert entry["schema"]["columns"] == ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"]
    assert entry["schema"]["key"] == ["TYPE", "SOURCE_VALUE"]


# ── the mapping is proposed, not demanded ────────────────────────────
def test_headers_are_matched_despite_accents_and_wording(tco):
    """A partner writing 'Désignation' and a schema saying TARGET_LABEL should
    not require anyone to type a mapping by hand — when the words do line up."""
    sid = _upload("Type;Source value;Target label\nCIVILITE;MR;MASCULIN\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/suggest",
                    json={"session_id": sid}).json()
    assert r["mapping"] == {"TYPE": "Type", "SOURCE_VALUE": "Source value",
                            "TARGET_LABEL": "Target label"}
    assert r["unmatched_target"] == []


def test_what_could_not_be_matched_is_reported_not_hidden(tco):
    sid = _upload()
    r = client.post(f"/api/targets/tco/{tco['id']}/suggest",
                    json={"session_id": sid}).json()
    # None of the partner's headers line up: say so rather than load empties.
    assert set(r["unmatched_target"]) == {"TYPE", "SOURCE_VALUE", "TARGET_LABEL"}
    assert "Code partenaire" in r["unused_file"]


# ── preview before touching anything ─────────────────────────────────
def test_the_preview_checks_against_the_target_without_a_config(tco):
    sid = _upload()
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_in"] == 3
    # The third row has no source value, and the schema requires one — caught
    # without anyone writing a rule.
    assert body["rows_rejected"] == 1
    assert any(p["column"] == "SOURCE_VALUE" for p in body["problems"])
    assert body["preview"]["columns"] == ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"]


def test_a_column_absent_from_the_file_is_refused_up_front(tco):
    sid = _upload()
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid, "mapping": {"SOURCE_VALUE": "Colonne fantôme"}})
    assert r.status_code == 422 and "fantôme" in r.json()["detail"]


# ── loading ──────────────────────────────────────────────────────────
def test_a_load_appends_a_version_and_merges_on_the_key(tco):
    sid = _upload()
    r = client.post(f"/api/targets/tco/{tco['id']}/load", json={
        "session_id": sid, "mode": "append", "policy": "reject",
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_loaded"] == 2 and body["rows_rejected"] == 1
    assert body["version_no"] == 2                 # never edited in place
    assert body["rows_total"] == 3                 # MLLE kept, MR and MME added

    got = client.get(f"/api/artefacts/tco/{tco['id']}/versions/2").json()
    csv = got["body"]["csv"]
    assert "MR;MASCULIN" in csv and "MLLE;FEMININ" in csv


def test_blocking_refuses_the_whole_load_rather_than_half_of_it(tco):
    """A half-applied load into a shared table is worse than none: nobody can
    tell which half."""
    sid = _upload()
    r = client.post(f"/api/targets/tco/{tco['id']}/load", json={
        "session_id": sid, "policy": "block",
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"}})
    assert r.status_code == 422 and "nothing was loaded" in r.json()["detail"]
    # still on version 1
    assert client.get(f"/api/artefacts/tco/{tco['id']}").json()["latest_version_no"] == 1


def test_a_duplicate_key_is_announced_before_it_overwrites(tco):
    sid = _upload("Famille;Code partenaire;Désignation\n"
                  "CIVILITE;MR;MASCULIN\nCIVILITE;MR;MONSIEUR\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"}}).json()
    assert r["duplicate_keys"] == 1


def test_extra_cleaning_can_be_asked_for_on_top_of_the_schema(tco):
    sid = _upload("Famille;Code partenaire;Désignation\nciv;  mr  ;masculin\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"},
        "rules": {"SOURCE_VALUE": {"normalize_case": "upper", "trim": True}}}).json()
    assert "MR" in str(r["preview"]["data"])


def test_a_confidential_column_cannot_be_laundered_into_a_shared_table(tco):
    """Loading must not be a way round the mark: the file's own configuration
    said this column is confidential."""
    sid = _upload("Famille;Code partenaire;Désignation\nCIV;MR;MASCULIN\n")
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["Famille", "Code partenaire", "Désignation"],
        "fields": {"Famille": {"name": ["Famille"], "type": "string"},
                   "Code partenaire": {"name": ["Code partenaire"], "type": "string"},
                   "Désignation": {"name": ["Désignation"], "type": "string",
                                   "sensitive": "paie"}}})
    r = client.post(f"/api/targets/tco/{tco['id']}/load", json={
        "session_id": sid,
        "mapping": {"SOURCE_VALUE": "Code partenaire",
                    "TARGET_LABEL": "Désignation", "TYPE": "Famille"}})
    assert r.status_code == 409 and "confidential" in r.json()["detail"]


def test_loading_into_a_stored_table_uses_its_recorded_schema():
    """A dataset records its columns and types when it is created, so a later
    load needs no configuration either."""
    sid = _upload("SIRET;MONTANT\n12345678901234;100\n")
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}})
    dsid = client.post(f"/api/files/{sid}/datasets/write", json={
        "name": f"cible-{os.urandom(3).hex()}", "mode": "replace",
        "policy": "all", "key_fields": ["SIRET"],
        "columns": ["SIRET", "MONTANT"]}).json()["dataset"]["id"]

    sid2 = _upload("Siret;Montant\n99999999999999;250\n")
    sug = client.post(f"/api/targets/dataset/{dsid}/suggest",
                      json={"session_id": sid2}).json()
    assert sug["mapping"] == {"SIRET": "Siret", "MONTANT": "Montant"}

    r = client.post(f"/api/targets/dataset/{dsid}/load", json={
        "session_id": sid2, "mode": "append", "mapping": sug["mapping"]})
    assert r.status_code == 200, r.text
    assert r.json()["rows_loaded"] == 1 and r.json()["rows_total"] == 2


# ══════════════════════════════════════════════════════════════════════
# Two layers: the inbound configuration treats, the target verifies
# ══════════════════════════════════════════════════════════════════════
def test_a_column_can_be_computed_from_others_and_then_loaded(tco):
    """The target schema cannot express this — which is precisely why it must
    not be the only thing describing the treatment."""
    sid = _upload("Famille;Code;Nom;Prenom\nCIVILITE;MR;Dupont;Jean\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "computed": [{"name": "libelle", "expression": "CONCAT([Prenom], ' ', [Nom])"}],
        "mapping": {"TYPE": "Famille", "SOURCE_VALUE": "Code",
                    "TARGET_LABEL": "libelle"}})
    assert r.status_code == 200, r.text
    assert "Jean Dupont" in str(r.json()["preview"]["data"])


def test_computed_columns_may_build_on_one_another():
    sid = _upload("A;B\n2;3\n")
    dsid = _make_dataset(sid)
    r = client.post(f"/api/targets/dataset/{dsid}/preview", json={
        "session_id": sid,
        "computed": [{"name": "somme", "expression": "NUM([A]) + NUM([B])"},
                     {"name": "double", "expression": "NUM([somme]) * 2"}],
        "mapping": {"SIRET": "somme", "MONTANT": "double"}})
    assert r.status_code == 200, r.text
    assert "10" in str(r.json()["preview"]["data"])      # (2+3) * 2


def test_the_inbound_configuration_cleans_before_the_target_checks(tco):
    """A date, a case fold, a trim: the shape check would reject values the
    treatment was supposed to fix first."""
    sid = _upload("Famille;Code;Designation\nciv;  mr  ;masculin\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "fields": {"Code": {"name": ["Code"], "type": "string",
                            "trim": True, "normalize_case": "upper"}},
        "mapping": {"TYPE": "Famille", "SOURCE_VALUE": "Code",
                    "TARGET_LABEL": "Designation"}}).json()
    assert "MR" in str(r["preview"]["data"])


def test_a_stored_configuration_can_drive_the_load(tco):
    cfg = client.post("/api/artefacts/config", json={
        "name": f"regles-{os.urandom(3).hex()}",
        "yaml": ("type: CSV\ndelimiter: \";\"\nFields:\n"
                 "  - name: [Code]\n    type: string\n    regex: \"^[A-Z]{2,4}$\"\n")}).json()
    sid = _upload("Famille;Code;Designation\nCIVILITE;mr123;MASCULIN\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid, "config_artefact_id": cfg["id"],
        "mapping": {"TYPE": "Famille", "SOURCE_VALUE": "Code",
                    "TARGET_LABEL": "Designation"}}).json()
    # The business rule caught it on the way in, and the report says where.
    inbound = [p for p in r["problems"] if p["stage"] == "entrée"]
    assert inbound and inbound[0]["column"] == "Code"


def test_the_report_distinguishes_an_inbound_failure_from_a_fit_failure(tco):
    """Different problems, different fixes: a business rule failing means the
    file is wrong; the target refusing means the treatment does not fit."""
    sid = _upload("Famille;Code;Designation\nCIVILITE;mr123;MASCULIN\nCIVILITE;;VIDE\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "fields": {"Code": {"name": ["Code"], "type": "string",
                            "regex": "^[A-Z]{2,4}$"}},
        "mapping": {"TYPE": "Famille", "SOURCE_VALUE": "Code",
                    "TARGET_LABEL": "Designation"}}).json()
    stages = {p["stage"] for p in r["problems"]}
    assert stages == {"entrée", "cible"}


def test_a_broken_expression_names_its_column_not_a_stack_trace(tco):
    sid = _upload("Famille;Code;Designation\nCIVILITE;MR;MASCULIN\n")
    r = client.post(f"/api/targets/tco/{tco['id']}/preview", json={
        "session_id": sid,
        "computed": [{"name": "casse", "expression": "NOPE("}],
        "mapping": {"TYPE": "Famille", "SOURCE_VALUE": "Code",
                    "TARGET_LABEL": "Designation"}})
    assert r.status_code == 422 and "casse" in r.json()["detail"]


def _make_dataset(sid_for_shape: str) -> str:
    """A target with a recorded schema, to load into."""
    sid = _upload("SIRET;MONTANT\n11111111111111;1\n")
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}})
    return client.post(f"/api/files/{sid}/datasets/write", json={
        "name": f"cible-{os.urandom(3).hex()}", "mode": "replace", "policy": "all",
        "key_fields": ["SIRET"], "columns": ["SIRET", "MONTANT"]}).json()["dataset"]["id"]
