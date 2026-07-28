"""
The sftp bricks: the remote sibling of hotfolder/file — same FIFO pick, same
parser, same archive/error finalisation, just over SFTP instead of the local
filesystem. Exercised against a fake paramiko client (no real SFTP server
needed) so these tests stay fast and hermetic; connection resolution, kind
checking and the run journal are the real thing underneath.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class FakeAttr:
    def __init__(self, filename, mtime):
        self.filename = filename
        self.st_mtime = mtime
        self.st_mode = 0o100644          # a regular file, never a directory


class FakeSFTP:
    """An in-memory stand-in for paramiko.SFTPClient — `files` maps a full
    remote path to its bytes, shared by every FakeSFTP instance in a test."""

    def __init__(self, files: dict):
        self.files = files

    def listdir_attr(self, path):
        prefix = path.rstrip("/") + "/"
        return [FakeAttr(name[len(prefix):], i)
                for i, name in enumerate(self.files) if name.startswith(prefix)]

    def getfo(self, path, buf):
        buf.write(self.files[path])

    def putfo(self, buf, path):
        self.files[path] = buf.read()

    def stat(self, path):
        if path not in self.files:
            raise IOError(f"no such file: {path}")

    def mkdir(self, path):
        pass

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def close(self):
        pass


class FakeTransport:
    def __init__(self, addr):
        pass

    def connect(self, username=None, password=None, pkey=None):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_sftp(monkeypatch):
    """Patches paramiko so both the brick (reading) and the run's finalisation
    step (moving the file afterwards) talk to the same fake remote files."""
    import paramiko

    files: dict = {}
    shared = FakeSFTP(files)
    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", staticmethod(lambda t: shared))
    return files


def _connection(name, remote_dir, archive_dir, error_dir):
    value = json.dumps({"host": "sftp.exemple.fr", "user": "fx", "password": "x",
                        "remote_dir": remote_dir, "archive_dir": archive_dir,
                        "error_dir": error_dir})
    r = client.post("/api/variables", json={"name": name, "value": value,
                                            "scope": "global", "kind": "sftp"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_file_found_over_sftp_is_parsed_into_records(fake_sftp):
    fake_sftp["/incoming/a.csv"] = b"A;B\n1;2\n"
    _connection("depot_sftp_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-lecture
nodes:
  - {id: src, type: sftp, config: {connection: "depot_sftp_v1", file_type: csv, delimiter: ";"}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 1


def test_no_file_and_not_required_yields_zero_rows(fake_sftp):
    _connection("depot_sftp_vide_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-vide
nodes:
  - {id: src, type: sftp, config: {connection: "depot_sftp_vide_v1", required: false}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 0


def test_no_file_and_required_by_default_blocks(fake_sftp):
    _connection("depot_sftp_bloque_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-bloque
nodes:
  - {id: src, type: sftp, config: {connection: "depot_sftp_bloque_v1"}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "src" in r.json()["detail"]


def test_a_connection_of_the_wrong_kind_is_refused(fake_sftp):
    r = client.post("/api/variables", json={"name": "pas_un_sftp_v1", "value": "1",
                                            "scope": "global", "kind": "value"})
    assert r.status_code == 200, r.text
    y = """
name: mauvais-kind
nodes:
  - {id: src, type: sftp, config: {connection: "pas_un_sftp_v1"}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "src" in r.json()["detail"]


def test_a_successful_run_archives_the_picked_file(fake_sftp):
    fake_sftp["/incoming/b.csv"] = b"A;B\n1;2\n"
    _connection("depot_sftp_archive_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-archive
nodes:
  - {id: src, type: sftp, config: {connection: "depot_sftp_archive_v1", file_type: csv, delimiter: ";"}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert "/incoming/b.csv" not in fake_sftp
    assert "/ok/b.csv" in fake_sftp


def test_a_failing_downstream_node_sends_the_file_to_error(fake_sftp):
    fake_sftp["/incoming/c.csv"] = b"A;B\n1;2\n"
    _connection("depot_sftp_erreur_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-erreur
nodes:
  - {id: src, type: sftp, config: {connection: "depot_sftp_erreur_v1", file_type: csv, delimiter: ";"}}
  - {id: boom, type: compute, config: {columns: {x: "NOPE("}}}
  - {id: out, type: response}
edges: [{from: src, to: boom}, {from: boom, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422
    assert "/incoming/c.csv" not in fake_sftp
    assert "/ko/c.csv" in fake_sftp


def test_sftp_write_uploads_the_current_data(fake_sftp):
    _connection("depot_sftp_ecriture_v1", "/incoming", "/ok", "/ko")
    y = """
name: sftp-ecriture
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1", b: "2"}]}}
  - id: out
    type: sftp_write
    config: {connection: "depot_sftp_ecriture_v1", filename: "sortie.csv"}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert "/incoming/sortie.csv" in fake_sftp


def test_the_sftp_bricks_are_in_the_palette():
    types = {x["type"]: x["role"] for x in client.get("/api/graphs/bricks").json()["bricks"]}
    assert types["sftp"] == "source"
    assert types["sftp_write"] == "sink"
    assert types["external_db"] == "source"
    assert types["external_db_write"] == "sink"
