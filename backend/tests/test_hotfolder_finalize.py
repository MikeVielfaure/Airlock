"""
After a run concludes, a hotfolder file is moved to its connection's archive
folder on success or its error folder on failure — never both, never in
place, never overwritten. This is where the whole slice comes together:
hotfolder -> config -> dataset_write -> email, with no branching primitive
needed because the graph is fail-fast.
"""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CONTRAT = """type: CSV
delimiter: ";"
Fields:
  - name: [MATRICULE]
    type: string
    regex: "^M\\\\d{4}$"
    nullable: false
"""


@pytest.fixture
def fake_smtp(monkeypatch):
    calls: list[dict] = []

    class _Fake:
        def __init__(self, host, port, timeout=None):
            calls.append({"host": host})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, to, msg):
            pass

    monkeypatch.setattr("smtplib.SMTP", _Fake)
    return calls


def _dirs():
    root = tempfile.mkdtemp(prefix="fx_hotfolder_finalize_")
    path = os.path.join(root, "in")
    archive = os.path.join(root, "ok")
    error = os.path.join(root, "ko")
    os.makedirs(path)
    return path, archive, error


def _connection(name, path, archive, error):
    value = json.dumps({"path": path, "archive_dir": archive, "error_dir": error})
    r = client.post("/api/variables", json={"name": name, "value": value,
                                            "scope": "global", "kind": "hotfolder"})
    assert r.status_code == 200, r.text


def _smtp_connection(name):
    client.post("/api/variables", json={"name": name,
                                        "value": json.dumps({"host": "smtp.exemple.fr"}),
                                        "scope": "global", "kind": "smtp"})


def _cfg(yaml_text, name):
    return client.post("/api/artefacts/config", json={"name": name, "yaml": yaml_text}).json()


def _write_file(path, name, content):
    with open(os.path.join(path, name), "wb") as f:
        f.write(content.encode())


def test_a_successful_run_archives_the_file_and_sends_the_mail(fake_smtp):
    path, archive, error = _dirs()
    _connection("depot_succes_v1", path, archive, error)
    _smtp_connection("relais_succes_v1")
    cfg = _cfg(CONTRAT, "contrat-succes-v1")
    _write_file(path, "bon.csv", "MATRICULE\nM0001\n")

    table = "table_succes_v1"
    y = f"""
name: chaine-succes
nodes:
  - {{id: hf, type: hotfolder, config: {{connection: "depot_succes_v1", file_type: csv, delimiter: ";"}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", on_error: block}}}}
  - {{id: db, type: dataset_write, config: {{name: "{table}", mode: replace}}}}
  - id: mail
    type: email
    config: {{connection: "relais_succes_v1", to: "ops@x.fr", subject: "ok", body: "ok"}}
edges: [{{from: hf, to: ctl}}, {{from: ctl, to: db}}, {{from: db, to: mail}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert len(fake_smtp) == 1                        # the mail really went out

    assert not os.path.exists(os.path.join(path, "bon.csv"))
    assert os.path.exists(os.path.join(archive, "bon.csv"))
    assert not os.path.exists(os.path.join(error, "bon.csv"))

    rows = client.get("/api/datasets").json()
    found = [d for d in rows if d["name"] == table]
    assert found and found[0]["row_count"] == 1


def test_a_failing_config_sends_the_file_to_error_and_never_calls_mail(fake_smtp):
    path, archive, error = _dirs()
    _connection("depot_echec_v1", path, archive, error)
    _smtp_connection("relais_echec_v1")
    cfg = _cfg(CONTRAT, "contrat-echec-v1")
    _write_file(path, "mauvais.csv", "MATRICULE\noups\n")

    y = f"""
name: chaine-echec
nodes:
  - {{id: hf, type: hotfolder, config: {{connection: "depot_echec_v1", file_type: csv, delimiter: ";"}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", on_error: block}}}}
  - {{id: db, type: dataset_write, config: {{name: "table_jamais_v1", mode: replace}}}}
  - id: mail
    type: email
    config: {{connection: "relais_echec_v1", to: "ops@x.fr", subject: "ok", body: "ok"}}
edges: [{{from: hf, to: ctl}}, {{from: ctl, to: db}}, {{from: db, to: mail}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422

    assert fake_smtp == []                             # never reached
    assert not os.path.exists(os.path.join(path, "mauvais.csv"))
    assert os.path.exists(os.path.join(error, "mauvais.csv"))
    assert not os.path.exists(os.path.join(archive, "mauvais.csv"))


def test_a_name_collision_in_archive_is_never_overwritten():
    path, archive, error = _dirs()
    os.makedirs(archive)
    _connection("depot_collision_v1", path, archive, error)
    _write_file(archive, "doublon.csv", "ANCIEN")
    _write_file(path, "doublon.csv", "A\n1\n")

    y = """
name: collision
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_collision_v1", file_type: csv}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text

    names = os.listdir(archive)
    assert "doublon.csv" in names                      # the old one survives
    assert len(names) == 2                              # and the new one too, renamed
    with open(os.path.join(archive, "doublon.csv")) as f:
        assert f.read() == "ANCIEN"


def test_the_destination_folder_is_created_if_missing():
    path, archive, error = _dirs()
    _connection("depot_sans_dossier_v1", path, archive, error)   # archive/ never created
    _write_file(path, "x.csv", "A\n1\n")

    y = """
name: sans-dossier
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_sans_dossier_v1", file_type: csv}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert os.path.exists(os.path.join(archive, "x.csv"))


def test_replaying_same_data_does_not_move_the_file_again():
    path, archive, error = _dirs()
    _connection("depot_replay_v1", path, archive, error)
    _write_file(path, "u.csv", "A\n1\n")

    y = """
name: replay-meme-donnee
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_replay_v1", file_type: csv}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    run_id = r.json()["run_id"]
    assert os.listdir(archive) == ["u.csv"]

    replay = client.post(f"/api/ops/runs/{run_id}/replay", json={"mode": "same_data"})
    assert replay.status_code == 200, replay.text
    # the source was never re-run, so nothing new should appear in archive
    assert os.listdir(archive) == ["u.csv"]


def test_replaying_refetch_picks_up_whatever_is_there_now():
    path, archive, error = _dirs()
    _connection("depot_refetch_v1", path, archive, error)
    _write_file(path, "premier.csv", "A\n1\n")

    y = """
name: replay-refetch
nodes:
  - {id: hf, type: hotfolder, config: {connection: "depot_refetch_v1", file_type: csv}}
  - {id: out, type: response}
edges: [{from: hf, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    run_id = r.json()["run_id"]
    assert os.listdir(archive) == ["premier.csv"]

    _write_file(path, "second.csv", "A\n2\n")
    replay = client.post(f"/api/ops/runs/{run_id}/replay", json={"mode": "refetch"})
    assert replay.status_code == 200, replay.text
    assert "second.csv" in os.listdir(archive)
