"""
Testing a draft connection before it is even saved — a live, best-effort
reachability check, deliberately separate from the flow bricks (which read
or write real data). Never raises: an unreachable host or a wrong password
is a result to report, not a server error.
"""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import connection_probe as ct

client = TestClient(app)


def _h(t):
    return {"Authorization": f"Bearer {t}"}


# ── the service, directly ────────────────────────────────────────────────
def test_hotfolder_reports_the_file_count():
    root = tempfile.mkdtemp(prefix="fx_conn_test_")
    with open(os.path.join(root, "a.csv"), "w") as f:
        f.write("a;b\n1;2\n")
    r = ct.test_connection("hotfolder", {"path": root})
    assert r["ok"] is True
    assert "1" in r["message"]


def test_hotfolder_missing_path_is_a_clean_failure():
    r = ct.test_connection("hotfolder", {"path": "/does/not/exist/xyz"})
    assert r["ok"] is False


def test_hotfolder_missing_field_is_a_clean_failure():
    r = ct.test_connection("hotfolder", {})
    assert r["ok"] is False


def test_external_db_reports_success_against_a_real_connection():
    r = ct.test_connection("external_db", {"url": "sqlite:///:memory:"})
    assert r["ok"] is True


def test_external_db_bad_url_is_a_clean_failure():
    r = ct.test_connection("external_db", {"url": "not-a-real-dsn://nope"})
    assert r["ok"] is False
    assert r["message"]


def test_api_reports_the_http_status(monkeypatch):
    from app.services import net_guard

    class FakeResp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    # La sonde ne passe plus par `urllib.request.urlopen` mais par
    # `net_guard.urlopen`, qui valide l'URL puis ouvre sans FileHandler.
    # Intercepter le vrai collaborateur garde ce test sur son sujet — le
    # statut rapporté — au lieu de le faire porter sur la plomberie.
    monkeypatch.setattr(net_guard, "urlopen", lambda req, timeout=0: FakeResp())
    r = ct.test_connection("api", {"base_url": "https://exemple.fr"})
    assert r["ok"] is True and "204" in r["message"]


def test_api_missing_base_url_is_a_clean_failure():
    r = ct.test_connection("api", {})
    assert r["ok"] is False


def test_api_an_http_error_is_still_reachability(monkeypatch):
    import urllib.error

    from app.services import net_guard

    def boom(req, timeout=0):
        raise urllib.error.HTTPError("https://exemple.fr", 404, "Not Found", {}, None)

    monkeypatch.setattr(net_guard, "urlopen", boom)
    r = ct.test_connection("api", {"base_url": "https://exemple.fr"})
    assert r["ok"] is True and "404" in r["message"]


def test_sftp_reports_the_entry_count(monkeypatch):
    import paramiko

    class FakeSFTP:
        def listdir(self, path):
            return ["a", "b", "c"]

        def close(self):
            pass

    class FakeTransport:
        def __init__(self, addr):
            pass

        def connect(self, **kw):
            pass

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", staticmethod(lambda t: FakeSFTP()))
    r = ct.test_connection("sftp", {"host": "h", "user": "u", "remote_dir": "/in", "password": "x"})
    assert r["ok"] is True and "3" in r["message"]


def test_unknown_kind_has_nothing_to_test():
    r = ct.test_connection("value", {})
    assert r["ok"] is False


def test_a_raised_exception_never_escapes(monkeypatch):
    import smtplib

    def boom(*a, **kw):
        raise ConnectionRefusedError("nope")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    r = ct.test_connection("smtp", {"host": "unreachable.invalid"})
    assert r["ok"] is False and "ConnectionRefusedError" in r["message"]


# ── the route ────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean():
    from app.db import session_scope
    from app.db_models import (AuthSession, CryptoKey, KeyHolder, Membership,
                               RevealEvent, User, UserIdentity)
    def wipe():
        with session_scope() as s:
            for m in (RevealEvent, KeyHolder, CryptoKey, AuthSession, UserIdentity,
                      Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


@pytest.fixture
def token():
    client.post("/api/auth/signup", json={"email": "dg@x.fr", "password": "motdepasse1"})
    return client.post("/api/auth/login",
                       json={"email": "dg@x.fr", "password": "motdepasse1"}).json()["token"]


def test_the_route_reports_a_clean_failure_not_a_server_error(token):
    r = client.post("/api/variables/test",
                    json={"kind": "hotfolder", "value": json.dumps({"path": "/nope/xyz"})},
                    headers=_h(token))
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_the_route_refuses_unparsable_json(token):
    r = client.post("/api/variables/test", json={"kind": "smtp", "value": "not json"},
                    headers=_h(token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
