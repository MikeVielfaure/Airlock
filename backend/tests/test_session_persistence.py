"""
The point of this chantier: a session is a row in the database, not a dict
living in one process's memory. `uvicorn --workers 2` splitting requests
across processes, or a container restart, must not lose in-progress work.

These tests prove it without actually spawning a second process: a *fresh*
`SessionStore` instance (no shared Python state with the one the app used)
reading the row back is the direct proof that nothing was cached in memory
— if it had been, a fresh instance with an empty `__init__` would have
nothing to find.
"""
import io

from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import app
from app.session import SessionStore

client = TestClient(app)

CSV = "CODE;LIBELLE\nFR;france\nBE;belgique\n"


def _upload(csv: str = CSV):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_a_session_survives_a_fresh_store_instance():
    """A brand new SessionStore() — no dict inherited from the one the app
    used — must still find the session: proof the row lives in the
    database, not in this process's memory."""
    sid = _upload()
    fresh_store = SessionStore()
    with session_scope() as s:
        sess = fresh_store.get(s, sid)
    assert sess.raw_df is not None
    assert list(sess.raw_df.columns) == ["CODE", "LIBELLE"]


def test_mutations_made_through_the_api_are_readable_from_a_fresh_store():
    """Edits made via ordinary routes (which mutate through `with
    store.session(...)`) must show up to a store instance that never saw
    the mutation happen — not just to the object the route already held."""
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/add", json={"count": 2})
    assert r.status_code == 200, r.text

    fresh_store = SessionStore()
    with session_scope() as s:
        sess = fresh_store.get(s, sid)
    assert len(sess.work_df) == 4          # 2 original rows + 2 added
    assert len(sess.added) == 2


def test_a_failed_request_does_not_persist_a_half_mutated_session():
    """An exception raised while `with store.session(...)` is open must
    discard the mutation — a partial edit writing nothing, same reasoning
    as a partial file load elsewhere in this app."""
    sid = _upload()
    with session_scope() as s:
        from app.session import store
        try:
            with store.session(s, sid) as sess:
                sess.edits_count = 999
                raise ValueError("boom")
        except ValueError:
            pass

    fresh_store = SessionStore()
    with session_scope() as s:
        sess = fresh_store.get(s, sid)
    assert sess.edits_count == 0


def test_dropping_a_session_removes_it_for_every_store_instance():
    sid = _upload()
    with session_scope() as s:
        from app.session import store
        store.drop(s, sid)

    fresh_store = SessionStore()
    with session_scope() as s:
        try:
            fresh_store.get(s, sid)
            assert False, "the session should be gone"
        except KeyError:
            pass


def test_health_reports_the_session_count_from_the_database():
    r0 = client.get("/api/health").json()
    _upload()
    r1 = client.get("/api/health").json()
    assert r1["sessions"] == r0["sessions"] + 1
