"""
Confidential columns: propagation first, encryption behind it.

Propagation is what stops a salary leaking through a derived column; encryption
is what makes a forgotten exit emit gibberish rather than the value.
"""
import io
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-maitresse-de-test-pour-la-suite")

from app.main import app                     # noqa: E402
from app.services import crypto_service as cs  # noqa: E402

client = TestClient(app)
CSV = "MATRICULE;SALAIRE;SERVICE\nM1;3000;RH\nM2;4500;ADV\n"


def _upload(text=CSV):
    return client.post("/api/files",
                       files={"file": ("p.csv", io.BytesIO(text.encode()), "text/csv")},
                       data={"file_type": "CSV", "encoding": "AUTO",
                             "delimiter": ";"}).json()["session_id"]


@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import CryptoKey, KeyHolder, RevealEvent, User
    def wipe():
        with session_scope() as s:
            for m in (RevealEvent, KeyHolder, CryptoKey, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


# ══════════════════════════════════════════════════════════════════════
# Propagation — useful with no cryptography at all
# ══════════════════════════════════════════════════════════════════════
def test_a_column_computed_from_a_confidential_one_becomes_confidential():
    """`[salaire] * 12` must not be a laundering mechanism."""
    out = cs.propagate({"SALAIRE": "paie"}, {"annuel": "[SALAIRE] * 12"})
    assert out["annuel"] == "paie"


def test_the_mark_survives_a_chain_of_derivations():
    out = cs.propagate({"SALAIRE": "paie"},
                       {"annuel": "[SALAIRE] * 12", "tranche": "LEFT([annuel], 2)"})
    assert out["tranche"] == "paie"          # two hops away, still marked


def test_a_column_derived_from_nothing_sensitive_stays_free():
    out = cs.propagate({"SALAIRE": "paie"}, {"initiale": "LEFT([SERVICE], 1)"})
    assert "initiale" not in out


def test_propagation_also_follows_named_columns_not_just_expressions():
    out = cs.propagate_through_columns({"SALAIRE": "paie"}, "total", ["SALAIRE"])
    assert out["total"] == "paie"


def test_the_run_returns_the_propagated_map():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MATRICULE", "SALAIRE", "SERVICE"],
        "computed": [{"name": "annuel", "expression": "[SALAIRE] * 12"}],
        "fields": {"MATRICULE": {"name": ["MATRICULE"], "type": "string"},
                   "SALAIRE": {"name": ["SALAIRE"], "type": "integer",
                               "sensitive": "paie"},
                   "SERVICE": {"name": ["SERVICE"], "type": "string"}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sensitivity"]["SALAIRE"] == "paie"
    assert body["sensitivity"]["annuel"] == "paie"       # propagated by the run


def test_confidential_values_are_masked_in_what_is_displayed():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MATRICULE", "SALAIRE"],
        "fields": {"MATRICULE": {"name": ["MATRICULE"], "type": "string"},
                   "SALAIRE": {"name": ["SALAIRE"], "type": "integer",
                               "sensitive": "paie"}}}).json()
    col = r["columns"].index("SALAIRE")
    assert all(row[col] == cs.MASK for row in r["data"])
    assert "3000" not in str(r["data"])


# ── found live, while building the confidentiality UI: the mask applied to
# the initial /process response was never enough on its own, because the
# grid, the report and the export each read from a different place. ────────

def test_the_report_masks_confidential_values_including_the_free_text_message():
    """
    The report's dedicated columns aren't the only carrier: `resultat` is a
    free-text message and can embed the raw value too (e.g. `check_type KO —
    "abc"`). The masking bug this closes used `getattr(dict, "colonne")` on a
    plain dict (always None — dicts aren't attribute-accessed) and referenced
    a field, `valeur_source`, that doesn't even exist on `ReportRow` — so the
    report was, in practice, never masked at all.
    """
    sid = _upload("MATRICULE;SALAIRE\nM1; 3000 \nM2;abc\n")
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MATRICULE", "SALAIRE"],
        "fields": {"MATRICULE": {"name": ["MATRICULE"], "type": "string"},
                   "SALAIRE": {"name": ["SALAIRE"], "type": "integer",
                               "sensitive": "paie", "check_type": True}}})
    assert r.status_code == 200, r.text
    body = r.json()
    report = [row for row in body["report"] if row["colonne"] == "SALAIRE"]
    assert len(report) == 2
    for row in report:
        assert row["valeur_originale"] == cs.MASK
        assert row["valeur_finale"] == cs.MASK
        assert row["resultat"] == cs.MASK
    assert "3000" not in r.text and "abc" not in r.text


def test_paginated_rows_are_masked_like_the_initial_preview():
    """The grid's actual data source once a table is validated is this route
    (`serverMode` in DataTable.tsx is simply `!!result`), not the /process
    response body — a mask applied only there never reaches the screen."""
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MATRICULE", "SALAIRE"],
        "fields": {"MATRICULE": {"name": ["MATRICULE"], "type": "string"},
                   "SALAIRE": {"name": ["SALAIRE"], "type": "integer",
                               "sensitive": "paie"}}})
    assert r.status_code == 200, r.text
    rows = client.get(f"/api/files/{sid}/rows?offset=0&limit=100")
    assert rows.status_code == 200, rows.text
    body = rows.json()
    col = body["columns"].index("SALAIRE")
    assert all(row[col] == cs.MASK for row in body["data"])
    assert "3000" not in rows.text and "4500" not in rows.text


def test_export_masks_confidential_columns():
    """A value masked on screen must not still leave in the clear the moment
    it's written to a file — export was never wired to sensitivity at all."""
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MATRICULE", "SALAIRE"],
        "fields": {"MATRICULE": {"name": ["MATRICULE"], "type": "string"},
                   "SALAIRE": {"name": ["SALAIRE"], "type": "integer",
                               "sensitive": "paie"}}})
    assert r.status_code == 200, r.text
    out = client.get(f"/api/files/{sid}/export?fmt=csv&filename=t&encoding=utf-8&delimiter=%3B")
    assert out.status_code == 200, out.text
    assert cs.MASK in out.text
    assert "3000" not in out.text and "4500" not in out.text


def test_the_unmapped_values_list_stops_quoting_confidential_data():
    """That list prints values verbatim — on a confidential column it would hand
    them over in the clear, which is exactly the leak being closed."""
    client.post("/api/files/x/process", json={}) if False else None
    from app.main import _mask_uncovered
    out = _mask_uncovered({"SALAIRE": [{"value": "3000", "count": 4}]}, {"SALAIRE": "paie"})
    assert out["SALAIRE"][0]["value"] == cs.MASK
    assert out["SALAIRE"][0]["count"] == 4       # the count is still useful


# ══════════════════════════════════════════════════════════════════════
# Encryption
# ══════════════════════════════════════════════════════════════════════
def test_a_value_round_trips_and_looks_like_nothing_in_between():
    wrapped = cs.new_data_key()
    token = cs.encrypt_value("3000", wrapped)
    assert token != "3000" and cs.is_encrypted(token)
    assert cs.decrypt_value(token, wrapped) == "3000"


def test_an_empty_value_stays_empty():
    """Hiding the absence of a value buys nothing and breaks nullability checks."""
    wrapped = cs.new_data_key()
    assert cs.encrypt_value("", wrapped) == ""


def test_encrypting_twice_does_not_double_wrap():
    wrapped = cs.new_data_key()
    once = cs.encrypt_value("3000", wrapped)
    assert cs.encrypt_value(once, wrapped) == once


def test_the_wrong_key_yields_a_mask_not_a_crash():
    a, b = cs.new_data_key(), cs.new_data_key()
    token = cs.encrypt_value("3000", a)
    assert cs.decrypt_value(token, b) == cs.MASK


def test_a_frame_is_encrypted_then_rendered_masked_or_revealed():
    wrapped = cs.new_data_key()
    df = pd.DataFrame({"M": ["M1"], "SALAIRE": ["3000"]})
    enc = cs.encrypt_frame(df, {"SALAIRE": "paie"}, {"paie": wrapped})
    assert cs.is_encrypted(enc["SALAIRE"][0])

    masked = cs.render_frame(enc, {"SALAIRE": "paie"}, {"paie": wrapped})
    assert masked["SALAIRE"][0] == cs.MASK

    shown = cs.render_frame(enc, {"SALAIRE": "paie"}, {"paie": wrapped}, reveal=["paie"])
    assert shown["SALAIRE"][0] == "3000"


def test_writing_refuses_when_the_key_is_gone():
    wrapped = cs.new_data_key()
    df = pd.DataFrame({"SALAIRE": ["3000"]})
    with pytest.raises(cs.KeyRevoked):
        cs.encrypt_frame(df, {"SALAIRE": "paie"}, {})     # key absent
    assert cs.encrypt_frame(df, {"SALAIRE": "paie"}, {"paie": wrapped}) is not None


# ══════════════════════════════════════════════════════════════════════
# Keys, holders, reveal trail
# ══════════════════════════════════════════════════════════════════════
def test_the_creator_holds_the_key_and_only_holders_may_add_others():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {chef}"}
    client.post("/api/auth/signup",
                json={"email": "paul@x.fr", "password": "motdepasse1"}, headers=H)

    k = client.post("/api/keys", json={"name": "paie"}, headers=H)
    assert k.status_code == 200, k.text
    assert k.json()["i_hold"] is True                 # a key nobody holds only destroys
    kid = k.json()["id"]

    paul = client.post("/api/auth/login",
                       json={"email": "paul@x.fr", "password": "motdepasse1"}).json()["token"]
    HP = {"Authorization": f"Bearer {paul}"}
    ko = client.post(f"/api/keys/{kid}/holders", json={"email": "paul@x.fr"}, headers=HP)
    assert ko.status_code == 403                      # not a holder yet

    ok = client.post(f"/api/keys/{kid}/holders", json={"email": "paul@x.fr"}, headers=H)
    assert ok.status_code == 200 and len(ok.json()["holders"]) == 2


def test_revealing_is_recorded_and_refused_to_non_holders():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {chef}"}
    client.post("/api/auth/signup",
                json={"email": "rh@x.fr", "password": "motdepasse1"}, headers=H)
    client.post("/api/admin/environments/default/members",
                json={"email": "rh@x.fr", "role": "editor"}, headers=H)
    client.post("/api/keys", json={"name": "paie"}, headers=H)

    from app.db import session_scope
    from app.db_models import CryptoKey
    with session_scope() as s:
        wrapped = s.query(CryptoKey).filter_by(name="paie").one().wrapped_key
    token = cs.encrypt_value("3000", wrapped)

    r = client.post("/api/keys/reveal",
                    json={"key_name": "paie", "values": [token],
                          "columns": ["SALAIRE"], "context": "rapport paie"}, headers=H)
    assert r.status_code == 200 and r.json()["values"] == ["3000"]

    trail = client.get("/api/keys/reveals", headers=H).json()
    assert trail[0]["user_email"] == "chef@x.fr"
    assert trail[0]["columns"] == ["SALAIRE"] and trail[0]["rows"] == 1

    rh = client.post("/api/auth/login",
                     json={"email": "rh@x.fr", "password": "motdepasse1"}).json()["token"]
    ko = client.post("/api/keys/reveal", json={"key_name": "paie", "values": [token]},
                     headers={"Authorization": f"Bearer {rh}"})
    assert ko.status_code == 403                      # in the environment, not a holder


def test_the_last_holder_cannot_be_removed():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {chef}"}
    k = client.post("/api/keys", json={"name": "paie"}, headers=H).json()
    r = client.delete(f"/api/keys/{k['id']}/holders/{k['holders'][0]['user_id']}", headers=H)
    assert r.status_code == 409                       # the bus factor, refused up front


def test_destroying_a_key_demands_its_name_and_then_locks_the_data_away():
    client.post("/api/auth/signup", json={"email": "chef@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {chef}"}
    k = client.post("/api/keys", json={"name": "paie"}, headers=H).json()

    assert client.delete(f"/api/keys/{k['id']}", headers=H).status_code == 422
    ok = client.delete(f"/api/keys/{k['id']}?confirm=paie", headers=H)
    assert ok.status_code == 200

    after = client.post("/api/keys/reveal",
                        json={"key_name": "paie", "values": ["x"]}, headers=H)
    assert after.status_code == 409                   # gone for good, and it says so


def test_the_option_is_unavailable_without_a_master_key(monkeypatch):
    monkeypatch.delenv("FX_MASTER_KEY", raising=False)
    assert cs.crypto_available() is False
    assert client.get("/api/keys/status").json()["available"] is False
    with pytest.raises(cs.CryptoUnavailable):
        cs.new_data_key()
