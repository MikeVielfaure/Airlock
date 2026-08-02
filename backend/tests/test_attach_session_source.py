"""
Attaching another open tab (session) as a source — the multi-tab workspace's
way of crossing two tables without a dataset in between. A snapshot at
attach time, read from the source session without ever writing it back.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _upload(csv: str) -> str:
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_attach_another_open_session_as_a_source():
    other_sid = _upload("A;B\n1;2\n3;4\n")
    working_sid = _upload("X;Y\n9;9\n")

    r = client.post(f"/api/files/{working_sid}/sources/session",
                    json={"name": "autre_onglet", "source_sid": other_sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "autre_onglet"
    assert body["row_count"] == 2
    assert set(body["columns"]) == {"A", "B"}

    listed = client.get(f"/api/files/{working_sid}/sources").json()
    assert len(listed) == 1 and listed[0]["name"] == "autre_onglet"


def test_source_session_not_found():
    working_sid = _upload("X;Y\n9;9\n")
    r = client.post(f"/api/files/{working_sid}/sources/session",
                    json={"name": "fantome", "source_sid": "sid-inconnu"})
    assert r.status_code == 404


def test_a_session_source_needs_a_name():
    other_sid = _upload("A;B\n1;2\n")
    working_sid = _upload("X;Y\n9;9\n")
    r = client.post(f"/api/files/{working_sid}/sources/session",
                    json={"name": "  ", "source_sid": other_sid})
    assert r.status_code == 422


def test_a_session_source_too_large_is_refused():
    other_sid = _upload("A;B\n1;2\n3;4\n5;6\n")
    working_sid = _upload("X;Y\n9;9\n")
    import app.main as _main
    old_cap = _main.MAX_SOURCE_ROWS
    _main.MAX_SOURCE_ROWS = 1
    try:
        r = client.post(f"/api/files/{working_sid}/sources/session",
                        json={"name": "trop_grand", "source_sid": other_sid})
        assert r.status_code == 413
    finally:
        _main.MAX_SOURCE_ROWS = old_cap


def test_the_source_session_is_never_mutated():
    """Reading the source session for attachment must not save it back —
    attach is a read-only fetch, confirmed by the source's own preview
    (rows/columns) being unchanged after being used as a source."""
    other_sid = _upload("A;B\n1;2\n")
    working_sid = _upload("X;Y\n9;9\n")
    before = client.get(f"/api/files/{other_sid}/preview").json()

    client.post(f"/api/files/{working_sid}/sources/session",
               json={"name": "ref", "source_sid": other_sid})

    after = client.get(f"/api/files/{other_sid}/preview").json()
    assert before == after
