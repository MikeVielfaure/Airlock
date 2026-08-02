"""
A session becoming a correspondence table — the same doorway a fixed CSV
upload already is. Whatever brought the data in (file, blank session, SQL,
API) already left it as an ordinary working table; this just picks which
columns play SOURCE_VALUE / TARGET_LABEL rather than a second,
TCO-specific loading path.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.tco_service import TcoService

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_identity():
    """The one test below that signs up an account must not leave a
    superadmin behind — the moment an account exists the whole app closes,
    and every other test file after this one would start answering 401
    (see tests/test_auth.py::_clean, which this mirrors)."""
    from tests.test_auth import _wipe_identity
    _wipe_identity()
    yield
    _wipe_identity()

CSV = "CODE;LIBELLE;CATEGORIE\nFR;france;pays\nBE;belgique;pays\n"


def _upload(text: str = CSV):
    return client.post("/api/files",
                       files={"file": ("t.csv", io.BytesIO(text.encode()), "text/csv")},
                       data={"file_type": "CSV", "encoding": "AUTO",
                             "delimiter": ";"}).json()["session_id"]


def test_a_session_becomes_a_new_tco():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE", "name": "pays-tco"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_no"] == 1
    assert body["rows"] == 2

    ver = client.get(f"/api/artefacts/tco/{body['artefact_id']}/versions/1").json()
    df = TcoService().load_tco(ver["body"]["csv"].encode())
    assert set(df["SOURCE_VALUE"]) == {"FR", "BE"}
    assert set(df["TARGET_LABEL"]) == {"france", "belgique"}


def test_a_session_becomes_a_new_tco_with_a_type_column():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE",
        "type_column": "CATEGORIE", "name": "pays-tco-type"})
    assert r.status_code == 200, r.text
    ver = client.get(f"/api/artefacts/tco/{r.json()['artefact_id']}/versions/1").json()
    df = TcoService().load_tco(ver["body"]["csv"].encode())
    assert list(df.columns[:3]) == ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"]
    assert set(df["TYPE"]) == {"pays"}


def test_a_session_becomes_a_new_tco_with_a_fixed_type_value():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE", "type_value": "PAYS_NORM",
        "name": "pays-tco-fixed"})
    assert r.status_code == 200, r.text
    ver = client.get(f"/api/artefacts/tco/{r.json()['artefact_id']}/versions/1").json()
    df = TcoService().load_tco(ver["body"]["csv"].encode())
    assert set(df["TYPE"]) == {"PAYS_NORM"}


def test_a_missing_column_is_refused():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "INTROUVABLE", "name": "x"})
    assert r.status_code == 422
    assert "INTROUVABLE" in r.json()["detail"]


def test_an_empty_session_is_refused_rather_than_saved_as_an_empty_tco():
    sid = _upload("CODE;LIBELLE;CATEGORIE\n")   # header only, no data rows
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE", "name": "vide"})
    assert r.status_code == 422


def test_a_new_tco_needs_a_name():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE"})
    assert r.status_code == 422


def test_versioning_an_existing_tco_from_a_session():
    sid1 = _upload()
    first = client.post(f"/api/files/{sid1}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE", "name": "pays-tco-v"}).json()
    aid = first["artefact_id"]

    sid2 = _upload("CODE;LIBELLE;CATEGORIE\nDE;allemagne;pays\n")
    second = client.post(f"/api/files/{sid2}/to-tco", json={
        "source_column": "CODE", "target_column": "LIBELLE", "artefact_id": aid})
    assert second.status_code == 200, second.text
    assert second.json()["version_no"] == 2

    v1 = client.get(f"/api/artefacts/tco/{aid}/versions/1").json()
    assert "FR" in v1["body"]["csv"]           # the old version stays intact
    v2 = client.get(f"/api/artefacts/tco/{aid}/versions/2").json()
    assert "DE" in v2["body"]["csv"]


def test_versioning_a_tco_owned_by_another_environment_is_refused():
    """The capability gate alone checks the query-string `env` — appending
    a version writes into whatever environment *owns* the artefact, which
    can be a different one. An editor in "adv" must not be able to rewrite
    a TCO that belongs to "rh" just by passing ?env=adv."""
    from tests.test_auth import _h, _login, _signup

    _signup("tco-chef2@boite.fr")
    chef, _ = _login("tco-chef2@boite.fr")

    rh_sid = client.post("/api/files?env=rh",
                         files={"file": ("t.csv", io.BytesIO(CSV.encode()), "text/csv")},
                         data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                         headers=_h(chef)).json()["session_id"]
    made = client.post(f"/api/files/{rh_sid}/to-tco?env=rh", json={
        "source_column": "CODE", "target_column": "LIBELLE",
        "name": "rh-owned-tco", "environment": "rh"}, headers=_h(chef))
    assert made.status_code == 200, made.text
    aid = made.json()["artefact_id"]

    client.post("/api/admin/quick-user",
                json={"email": "tco-adv-editor@x.fr", "memberships": {"adv": "editor"}},
                headers=_h(chef))
    adv_editor, _ = _login("tco-adv-editor@x.fr")

    adv_sid = client.post("/api/files?env=adv",
                          files={"file": ("t.csv", io.BytesIO(CSV.encode()), "text/csv")},
                          data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                          headers=_h(adv_editor)).json()["session_id"]
    r = client.post(f"/api/files/{adv_sid}/to-tco?env=adv", json={
        "source_column": "CODE", "target_column": "LIBELLE", "artefact_id": aid},
        headers=_h(adv_editor))
    assert r.status_code == 403, r.text


def test_the_route_needs_the_tco_replace_capability():
    from tests.test_auth import _h, _login, _signup

    _signup("tco-chef@boite.fr")
    chef, _ = _login("tco-chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "tco-op@rh.fr", "memberships": {"rh": "operator"}},
                headers=_h(chef))
    op, _ = _login("tco-op@rh.fr")

    up = client.post("/api/files?env=rh",
                     files={"file": ("t.csv", io.BytesIO(CSV.encode()), "text/csv")},
                     data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
                     headers=_h(op))
    assert up.status_code == 200, up.text
    sid = up.json()["session_id"]
    r = client.post(f"/api/files/{sid}/to-tco?env=rh", json={
        "source_column": "CODE", "target_column": "LIBELLE", "name": "x"}, headers=_h(op))
    assert r.status_code == 403       # operator does not have tco.replace

    r2 = client.post(f"/api/files/{sid}/to-tco?env=rh", json={
        "source_column": "CODE", "target_column": "LIBELLE", "name": "x"}, headers=_h(chef))
    assert r2.status_code == 200, r2.text
