"""
The email brick: a terminal that sends a notification through an smtp
connection point. Chained after another sink, it only ever runs because the
DAG is fail-fast — there is no branching primitive to build for "only on
success", the graph already provides it.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture
def fake_smtp(monkeypatch):
    """Records every call instead of opening a real socket."""
    calls: list[dict] = []

    class _Fake:
        def __init__(self, host, port, timeout=None):
            self.record = {"host": host, "port": port, "tls": False,
                           "login": None, "sent": None}
            calls.append(self.record)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            self.record["tls"] = True

        def login(self, user, password):
            self.record["login"] = (user, password)

        def sendmail(self, sender, to, msg):
            self.record["sent"] = (sender, to, msg)

    monkeypatch.setattr("smtplib.SMTP", _Fake)
    return calls


def _smtp_connection(name, **fields):
    r = client.post("/api/variables", json={"name": name, "value": json.dumps(fields),
                                            "scope": "global", "kind": "smtp"})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_email_is_sent_through_the_connection(fake_smtp):
    _smtp_connection("relais_v1", host="smtp.exemple.fr", user="bot", password="s3cr3t")
    y = """
name: mail
params:
  - {name: qui, default: "personne"}
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - id: mail
    type: email
    config: {connection: "relais_v1", to: "ops@x.fr", subject: "Bonjour {qui}", body: "Termine."}
edges: [{from: src, to: mail}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y, "params": {"qui": "Marie"}})
    assert r.status_code == 200, r.text
    assert len(fake_smtp) == 1
    call = fake_smtp[0]
    assert call["host"] == "smtp.exemple.fr"
    assert call["port"] == 587 and call["tls"] is True         # defaults
    assert call["login"] == ("bot", "s3cr3t")
    sender, to, msg = call["sent"]
    assert to == ["ops@x.fr"]
    assert "Bonjour Marie" in msg


def test_a_missing_connection_is_refused(fake_smtp):
    y = """
name: mail-ko
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: mail, type: email, config: {connection: "jamais-vue-v1", to: "x@y.fr"}}
edges: [{from: src, to: mail}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "mail" in r.json()["detail"]
    assert fake_smtp == []


def test_a_connection_of_the_wrong_kind_is_refused(fake_smtp):
    client.post("/api/variables", json={"name": "pas_smtp_v1", "value": "42",
                                        "scope": "global", "kind": "value"})
    y = """
name: mail-mauvais-kind
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: mail, type: email, config: {connection: "pas_smtp_v1", to: "x@y.fr"}}
edges: [{from: src, to: mail}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422
    assert fake_smtp == []


def test_a_network_failure_names_the_node(monkeypatch):
    _smtp_connection("relais_panne_v1", host="smtp.exemple.fr")

    class _Boom:
        def __init__(self, *a, **kw):
            raise ConnectionRefusedError("no route to host")

    monkeypatch.setattr("smtplib.SMTP", _Boom)
    y = """
name: mail-panne
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: mail, type: email, config: {connection: "relais_panne_v1", to: "x@y.fr"}}
edges: [{from: src, to: mail}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "mail" in r.json()["detail"]
