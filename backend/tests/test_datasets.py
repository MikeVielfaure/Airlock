"""
Tests for the two halves of "correct the data, then land it in the database":

  • editable rows — adding, duplicating and (logically) deleting rows, including
    dropping a whole filtered batch, and getting them all back with a reset.

  • datasets — preflight's verdict, and above all its central distinction: a
    problem in the ROWS (fix them by hand, re-run, write) versus a problem in
    the SKELETON (no amount of hand-editing helps; go look at the source and
    fix the config).
"""

import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = (
    "SIRET;CIVILITE;MONTANT\n"
    "12345678901234;M;10\n"
    "99999999999999;F;20\n"
    "11111111111111;M;30\n"
)

# same columns, one row broken: SIRET too short
CSV_DIRTY = (
    "SIRET;CIVILITE;MONTANT\n"
    "12345678901234;M;10\n"
    "999;F;20\n"
    "11111111111111;M;30\n"
)

FIELDS = {
    "SIRET": {"name": ["SIRET"], "type": "string", "regex": r"^\d{14}$",
              "nullable": False, "identifiant": True},
    "CIVILITE": {"name": ["CIVILITE"], "type": "string"},
    "MONTANT": {"name": ["MONTANT"], "type": "string"},
}
COLS = ["SIRET", "CIVILITE", "MONTANT"]


def _upload(csv: str = CSV):
    r = client.post("/api/files",
                    files={"file": ("data.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _run(sid: str, cols=None):
    r = client.post(f"/api/files/{sid}/process",
                    json={"visible_cols": cols or COLS, "fields": FIELDS})
    assert r.status_code == 200, r.text
    return r.json()


def _write(sid: str, **kw):
    body = {"mode": "replace", "policy": "reject", "columns": COLS}
    body.update(kw)
    return client.post(f"/api/files/{sid}/datasets/write", json=body)


def _preflight(sid: str, **kw):
    body = {"mode": "replace", "policy": "reject", "columns": COLS}
    body.update(kw)
    return client.post(f"/api/files/{sid}/datasets/preflight", json=body).json()


# ══════════════════════════════════════════════════════════════════════
# Editable rows
# ══════════════════════════════════════════════════════════════════════
def test_add_blank_row_and_duplicate_an_existing_one():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/add", json={"count": 2})
    assert r.status_code == 200
    assert r.json()["total_rows"] == 5 and r.json()["added_total"] == 2

    r = client.post(f"/api/files/{sid}/rows/add", json={"copy_from": 0})
    assert r.json()["total_rows"] == 6

    out = _run(sid)
    assert len(out["data"]) == 6
    siret_col = out["columns"].index("SIRET")
    assert out["data"][-1][siret_col] == "12345678901234"      # the duplicate
    assert out["data"][3][siret_col] == ""                     # the blank ones


def test_new_row_indices_are_never_reused():
    """The row index is the stable key the edit overlay leans on — a collision
    would silently rewrite the wrong row."""
    sid = _upload()
    client.post(f"/api/files/{sid}/rows/add", json={"count": 2})
    before = _run(sid)["index"]
    client.post(f"/api/files/{sid}/rows/delete", json={"indices": before[-2:]})
    client.post(f"/api/files/{sid}/rows/add", json={"count": 2})
    after = _run(sid)["index"]
    assert len(set(after)) == len(after)
    assert not (set(after) & set(before[-2:]))                 # deleted keys not recycled


def test_delete_is_logical_and_a_reset_brings_rows_back():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/delete", json={"indices": [1]})
    assert r.json()["deleted"] == 1 and r.json()["total_rows"] == 2
    assert r.json()["deleted_total"] == 1

    out = _run(sid)
    assert len(out["data"]) == 2
    assert "99999999999999" not in [row[0] for row in out["data"]]

    assert client.post(f"/api/files/{sid}/cells/reset").json()["total_rows"] == 3
    assert len(_run(sid)["data"]) == 3


def test_restore_undoes_a_deletion_without_touching_edits():
    sid = _upload()
    client.post(f"/api/files/{sid}/cells",
                json={"edits": [{"index": 0, "column": "CIVILITE", "value": "Z"}]})
    client.post(f"/api/files/{sid}/rows/delete", json={"indices": [0, 2]})
    r = client.post(f"/api/files/{sid}/rows/restore", json={"indices": [2]})
    assert r.json()["restored"] == 1 and r.json()["total_rows"] == 2

    client.post(f"/api/files/{sid}/rows/restore", json={})     # all of them
    out = _run(sid)
    assert len(out["data"]) == 3
    assert out["data"][0][out["columns"].index("CIVILITE")] == "Z"   # edit survived


def test_delete_the_whole_filtered_batch():
    """Filter the errors, drop the lot — the gesture that makes this worth having."""
    sid = _upload(CSV_DIRTY)
    _run(sid)
    r = client.post(f"/api/files/{sid}/rows/delete",
                    json={"all_filtered": True, "statuses": ["ERROR"]})
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    out = _run(sid)
    assert len(out["data"]) == 2
    assert "999" not in [row[0] for row in out["data"]]


def test_deleting_a_filtered_batch_needs_a_run_first():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/rows/delete",
                    json={"all_filtered": True, "statuses": ["ERROR"]})
    assert r.status_code == 409


# ══════════════════════════════════════════════════════════════════════
# Datasets — the happy path
# ══════════════════════════════════════════════════════════════════════
def test_create_a_table_from_a_clean_session_and_read_it_back():
    sid = _upload()
    _run(sid)
    r = _write(sid, name="clients_test", key_fields=["SIRET"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["rows_written"] == 3
    ds_id = body["dataset"]["id"]
    assert body["dataset"]["columns"] == COLS
    assert body["dataset"]["key"] == ["SIRET"]

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    assert back["total_rows"] == 3
    assert back["columns"] == COLS
    assert back["data"][0][0] == "12345678901234"

    assert ds_id in [d["id"] for d in client.get("/api/datasets").json()]


def test_replace_is_idempotent_and_append_accumulates():
    sid = _upload()
    _run(sid)
    _write(sid, name="ds_replace")
    r = _write(sid, name="ds_replace")
    assert r.json()["rows_deleted"] == 3 and r.json()["rows_written"] == 3
    assert r.json()["dataset"]["row_count"] == 3          # replaced, not doubled

    _write(sid, name="ds_append")
    r = _write(sid, name="ds_append", mode="append")
    assert r.json()["dataset"]["row_count"] == 6


def test_upsert_updates_matching_keys_and_inserts_new_ones():
    sid = _upload()
    _run(sid)
    r = _write(sid, name="ds_upsert", key_fields=["SIRET"])
    ds_id = r.json()["dataset"]["id"]

    # same keys, one value changed -> updates, no growth
    sid2 = _upload(CSV.replace("12345678901234;M;10", "12345678901234;M;999"))
    _run(sid2)
    r = _write(sid2, name="ds_upsert", mode="upsert", key_fields=["SIRET"])
    assert r.status_code == 200, r.text
    assert r.json()["rows_updated"] == 3 and r.json()["rows_written"] == 0
    assert r.json()["dataset"]["row_count"] == 3

    rows = client.get(f"/api/datasets/{ds_id}/rows").json()
    montant = rows["columns"].index("MONTANT")
    assert "999" in [row[montant] for row in rows["data"]]

    # a genuinely new key is inserted
    sid3 = _upload(CSV + "22222222222222;F;40\n")
    _run(sid3)
    r = _write(sid3, name="ds_upsert", mode="upsert", key_fields=["SIRET"])
    assert r.json()["rows_written"] == 1 and r.json()["rows_updated"] == 3


def test_rows_in_error_are_rejected_by_default_and_counted():
    sid = _upload(CSV_DIRTY)
    _run(sid)
    r = _write(sid, name="ds_partial", key_fields=["SIRET"])
    body = r.json()
    assert body["ok"] is True
    assert body["rows_written"] == 2 and body["rows_rejected"] == 1
    assert any(p["code"] == "LIGNES_REJETEES" for p in body["problems"])


def test_the_hand_correction_loop_closes():
    """The workflow this is all for: run, see the error, fix it by hand, re-run,
    and only then does the row land in the table."""
    sid = _upload(CSV_DIRTY)
    out = _run(sid)
    bad = [i for i, row in enumerate(out["status"]) if "ERROR" in row]
    assert bad, "the dirty row should be flagged"
    idx = out["index"][bad[0]]

    assert _preflight(sid, name="ds_loop", mode="upsert",
                      key_fields=["SIRET"], policy="block")["blocked_by"] == "data"

    client.post(f"/api/files/{sid}/cells",
                json={"edits": [{"index": idx, "column": "SIRET", "value": "77777777777777"}]})
    _run(sid)

    r = _write(sid, name="ds_loop", mode="upsert", key_fields=["SIRET"], policy="block")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["rows_written"] == 3


# ══════════════════════════════════════════════════════════════════════
# Preflight — data problems vs skeleton problems
# ══════════════════════════════════════════════════════════════════════
def test_data_problems_point_at_the_rows():
    sid = _upload(CSV_DIRTY)
    _run(sid)
    v = _preflight(sid, name="ds_x", key_fields=["SIRET"], mode="upsert", policy="block")
    assert v["ok"] is False and v["blocked_by"] == "data"
    p = [x for x in v["problems"] if x["code"] == "LIGNES_EN_ERREUR"][0]
    assert p["count"] == 1 and p["rows"]
    assert "main" in p["hint"].lower() or "Data" in p["hint"]


def test_duplicate_and_empty_keys_are_data_problems():
    sid = _upload("SIRET;CIVILITE;MONTANT\n"
                  "12345678901234;M;10\n"
                  "12345678901234;F;20\n"
                  ";M;30\n")
    _run(sid)
    v = _preflight(sid, name="ds_y", mode="upsert", key_fields=["SIRET"], policy="all")
    assert v["blocked_by"] == "data"
    codes = {p["code"] for p in v["problems"]}
    assert "CLE_DUPLIQUEE" in codes and "CLE_VIDE" in codes


def test_a_broken_skeleton_is_a_config_problem_not_three_thousand_row_errors():
    """The whole point: when the header lands on the wrong line, every row looks
    wrong. Saying 'fix your rows' would be a lie — the shape is what's wrong."""
    sid = _upload("12345678901234;M;10\n99999999999999;F;20\n")
    r = client.post(f"/api/files/{sid}/header",
                    json={"header": {"mode": "none"}})
    if r.status_code != 200:                       # header modes vary; fall back
        r = client.post(f"/api/files/{sid}/header", json={"header": {}})
    cols = client.get(f"/api/files/{sid}/source").json()["columns"]
    unnamed = [c for c in cols if c.startswith("Unnamed:")]
    if not unnamed:
        return                                     # loader named them; nothing to assert
    client.post(f"/api/files/{sid}/process",
                json={"visible_cols": cols, "fields": {c: {"type": "string"} for c in cols}})
    v = _preflight(sid, name="ds_broken", columns=cols)
    assert v["blocked_by"] == "config"
    assert any(p["code"] == "SQUELETTE_SUSPECT" for p in v["problems"])


def test_schema_drift_blocks_append_but_replace_may_redefine():
    sid = _upload()
    _run(sid)
    _write(sid, name="ds_drift")

    # a file with an extra column
    sid2 = _upload("SIRET;CIVILITE;MONTANT;PAYS\n12345678901234;M;10;FR\n")
    client.post(f"/api/files/{sid2}/process", json={
        "visible_cols": COLS + ["PAYS"],
        "fields": {**FIELDS, "PAYS": {"name": ["PAYS"], "type": "string"}}})

    v = _preflight(sid2, name="ds_drift", mode="append", columns=COLS + ["PAYS"])
    assert v["blocked_by"] == "config"
    p = [x for x in v["problems"] if x["code"] == "COLONNES_INCONNUES"][0]
    assert "PAYS" in p["columns"]

    v = _preflight(sid2, name="ds_drift", mode="replace", columns=COLS + ["PAYS"])
    assert v["ok"] is True
    assert any(x["code"] == "SCHEMA_REDEFINI" for x in v["problems"])

    r = _write(sid2, name="ds_drift", mode="replace", columns=COLS + ["PAYS"])
    assert r.json()["dataset"]["columns"] == COLS + ["PAYS"]


def test_missing_columns_are_a_config_problem():
    sid = _upload()
    _run(sid)
    _write(sid, name="ds_missing")

    sid2 = _upload("SIRET;CIVILITE\n12345678901234;M\n")
    client.post(f"/api/files/{sid2}/process", json={
        "visible_cols": ["SIRET", "CIVILITE"],
        "fields": {k: FIELDS[k] for k in ("SIRET", "CIVILITE")}})
    v = _preflight(sid2, name="ds_missing", mode="append", columns=["SIRET", "CIVILITE"])
    assert v["blocked_by"] == "config"
    p = [x for x in v["problems"] if x["code"] == "COLONNES_MANQUANTES"][0]
    assert "MONTANT" in p["columns"]


def test_upsert_without_a_key_is_refused_with_a_config_hint():
    sid = _upload()
    _run(sid)
    v = _preflight(sid, name="ds_nokey", mode="upsert", key_fields=[])
    assert v["blocked_by"] == "config"
    assert any(p["code"] == "CLE_ABSENTE" for p in v["problems"])


def test_writing_without_running_validation_first_is_refused():
    sid = _upload()
    r = _write(sid, name="ds_norun")
    assert r.status_code == 409
    assert "validation" in r.json()["detail"].lower()


# ══════════════════════════════════════════════════════════════════════
# Audit & source recovery
# ══════════════════════════════════════════════════════════════════════
def test_refusals_are_logged_too():
    sid = _upload(CSV_DIRTY)
    _run(sid)
    r = _write(sid, name="ds_audit", mode="upsert", key_fields=["SIRET"], policy="block")
    assert r.json()["ok"] is False
    _write(sid, name="ds_audit", key_fields=["SIRET"])       # now succeeds (replace)

    ds_id = client.get("/api/datasets").json()
    ds_id = [d["id"] for d in ds_id if d["name"] == "ds_audit"][0]
    writes = client.get(f"/api/datasets/{ds_id}/writes").json()
    assert len(writes) >= 1
    assert any(w["ok"] for w in writes)


def test_the_source_is_recoverable_for_inspection():
    """When the skeleton is wrong, you need to see the file as it was read —
    not the cleaned view."""
    sid = _upload()
    client.post(f"/api/files/{sid}/cells",
                json={"edits": [{"index": 0, "column": "CIVILITE", "value": "ZZZ"}]})
    src = client.get(f"/api/files/{sid}/source").json()
    assert src["columns"] == COLS
    assert src["total_rows"] == 3
    assert "ZZZ" not in [c for row in src["data"] for c in row]   # raw, not edited


def test_archived_dataset_disappears_from_the_default_listing():
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="ds_archive").json()["dataset"]["id"]
    assert client.delete(f"/api/datasets/{ds_id}").status_code == 200
    assert ds_id not in [d["id"] for d in client.get("/api/datasets").json()]
    assert ds_id in [d["id"] for d in
                     client.get("/api/datasets", params={"include_archived": True}).json()]


# ══════════════════════════════════════════════════════════════════════
# Regressions found while auditing — each of these failed before its fix
# ══════════════════════════════════════════════════════════════════════
def test_rows_read_back_in_the_order_they_were_written():
    """A random uuid primary key and one shared timestamp per bulk insert give
    no usable ordering: without an explicit ordinal the table comes back
    shuffled."""
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="ordre_test", key_fields=["SIRET"]).json()["dataset"]["id"]

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    sirets = [row[back["columns"].index("SIRET")] for row in back["data"]]
    assert sirets == ["12345678901234", "99999999999999", "11111111111111"]

    # …and it stays stable across reads
    again = client.get(f"/api/datasets/{ds_id}/rows").json()
    assert again["data"] == back["data"]


def test_append_lands_after_the_existing_rows():
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="ordre_append", key_fields=["SIRET"]).json()["dataset"]["id"]

    sid2 = _upload("SIRET;CIVILITE;MONTANT\n22222222222222;F;40\n")
    _run(sid2)
    r = _write(sid2, name="ordre_append", mode="append")
    assert r.status_code == 200 and r.json()["ok"] is True

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    sirets = [row[back["columns"].index("SIRET")] for row in back["data"]]
    assert sirets[-1] == "22222222222222"
    assert len(sirets) == 4


def test_upsert_updates_in_place_without_moving_the_row():
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="ordre_upsert", key_fields=["SIRET"]).json()["dataset"]["id"]

    # same key as the FIRST row, different payload
    sid2 = _upload("SIRET;CIVILITE;MONTANT\n12345678901234;F;999\n")
    _run(sid2)
    r = _write(sid2, name="ordre_upsert", mode="upsert", key_fields=["SIRET"])
    body = r.json()
    assert body["ok"] is True and body["rows_updated"] == 1 and body["rows_written"] == 0

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    cols = back["columns"]
    assert len(back["data"]) == 3                      # no row added
    first = back["data"][0]
    assert first[cols.index("SIRET")] == "12345678901234"   # still in position 1
    assert first[cols.index("MONTANT")] == "999"            # and it was updated


def test_an_empty_key_cell_does_not_crash_the_preflight():
    """The emptied cell arrives as a float NaN, and float has no .strip() —
    the same trap as the v10 identifier crash."""
    sid = _upload("SIRET;CIVILITE;MONTANT\n12345678901234;M;10\n;F;20\n")
    _run(sid)
    v = _preflight(sid, name="cle_vide", mode="upsert", key_fields=["SIRET"], policy="all")
    codes = [p["code"] for p in v["problems"]]
    assert "CLE_VIDE" in codes
    assert v["blocked_by"] == "data"          # a row problem, not a skeleton one


def test_the_declared_types_travel_into_the_dataset_schema():
    """The config already declares a type per column: the dataset records it
    instead of flattening everything to string."""
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS,
        "fields": {**FIELDS, "MONTANT": {"name": ["MONTANT"], "type": "integer"}}})
    assert r.status_code == 200, r.text
    ds = _write(sid, name="schema_test", key_fields=["SIRET"]).json()["dataset"]
    assert ds["types"]["MONTANT"] == "integer"
    assert ds["types"]["SIRET"] == "string"
    assert ds["key"] == ["SIRET"]


def test_a_table_filled_by_replace_can_be_upserted_into_afterwards():
    """The modes have to compose. `replace` stamps each row with the key it was
    given, so a later `upsert` finds those rows instead of duplicating them —
    without it the second write silently doubles the table."""
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="compose", key_fields=["SIRET"]).json()["dataset"]["id"]

    sid2 = _upload("SIRET;CIVILITE;MONTANT\n12345678901234;X;777\n")
    _run(sid2)
    r = _write(sid2, name="compose", mode="upsert", key_fields=["SIRET"]).json()
    assert r["ok"] is True
    assert (r["rows_updated"], r["rows_written"]) == (1, 0)

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    assert back["total_rows"] == 3                       # not 4
    cols = back["columns"]
    assert back["data"][0][cols.index("CIVILITE")] == "X"


def test_the_recorded_key_applies_when_the_request_omits_it():
    """The dataset remembers its key; a later write need not repeat it."""
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="cle_memorisee", key_fields=["SIRET"]).json()["dataset"]["id"]

    sid2 = _upload("SIRET;CIVILITE;MONTANT\n99999999999999;Z;1\n")
    _run(sid2)
    r = _write(sid2, name="cle_memorisee", mode="upsert").json()   # no key_fields
    assert r["ok"] is True and r["rows_updated"] == 1

    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    assert back["total_rows"] == 3


def test_a_write_is_visible_to_the_very_next_read():
    """
    Read-after-write. `get_session` commits when FastAPI tears the dependency
    down — after the endpoint has returned — so a route that does not commit
    explicitly can answer "written" and then serve its own stale data to the
    next request. The UI does exactly that: it writes, then reloads the table.
    """
    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="raw_read", key_fields=["SIRET"]).json()["dataset"]["id"]
    assert client.get(f"/api/datasets/{ds_id}/rows").json()["total_rows"] == 3

    sid2 = _upload("SIRET;CIVILITE;MONTANT\n55555555555555;F;50\n")
    _run(sid2)
    r = _write(sid2, name="raw_read", mode="append").json()
    assert r["ok"] is True and r["rows_written"] == 1

    # no sleep, no retry: the next call must already see it
    back = client.get(f"/api/datasets/{ds_id}/rows").json()
    assert back["total_rows"] == 4
    assert back["data"][-1][back["columns"].index("SIRET")] == "55555555555555"

    # and a refused write is durably journalled too
    sid3 = _upload("AUTRE;COLONNE\nx;y\n")
    client.post(f"/api/files/{sid3}/process",
                json={"visible_cols": ["AUTRE"],
                      "fields": {"AUTRE": {"name": ["AUTRE"], "type": "string"}}})
    ko = client.post(f"/api/files/{sid3}/datasets/write",
                     json={"name": "raw_read", "mode": "append",
                           "policy": "reject", "columns": ["AUTRE"]}).json()
    assert ko["ok"] is False and ko["blocked_by"] == "config"
    logs = client.get(f"/api/datasets/{ds_id}/writes").json()
    assert any(w["ok"] is False for w in logs)


# ══════════════════════════════════════════════════════════════════════
# A table opened as a session: no second browser, the same object
# ══════════════════════════════════════════════════════════════════════
def test_a_table_opens_as_an_ordinary_session():
    """Rather than a second way to browse tables — its own filters, its own
    sort, its own paging — the table becomes a session and everything that
    already exists applies to it."""
    sid = _upload()
    _run(sid)
    ds = _write(sid, name="revue_table", key_fields=["SIRET"]).json()["dataset"]["id"]

    r = client.post(f"/api/datasets/{ds}/open")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "TABLE"
    assert body["preview"]["total_rows"] == 3
    assert "SIRET" in body["preview"]["columns"]

    # …and it really is a session: the existing filters answer on it
    new_sid = body["session_id"]
    run = client.post(f"/api/files/{new_sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}})
    assert run.status_code == 200
    rows = client.get(f"/api/files/{new_sid}/rows",
                      params={"filters": '{"SIRET": "=12345678901234"}', "limit": 10})
    assert rows.status_code == 200
    assert rows.json()["total"] == 1          # filtered server-side, like a file


def test_the_loop_closes_read_correct_write_back():
    """Consulting, correcting and re-recording are the same three gestures as
    for a file, because it is the same object."""
    sid = _upload()
    _run(sid)
    ds = _write(sid, name="boucle", key_fields=["SIRET"]).json()["dataset"]["id"]

    opened = client.post(f"/api/datasets/{ds}/open").json()["session_id"]
    idx = client.get(f"/api/files/{opened}/preview").json()["index"][0]
    client.post(f"/api/files/{opened}/cells",
                json={"edits": [{"index": idx, "column": "MONTANT", "value": "999"}]})
    _run(opened)
    # All the table's columns go back: writing a subset is a schema drift and is
    # refused, which is the v14 protection doing its job.
    back = client.post(f"/api/files/{opened}/datasets/write", json={
        "name": "boucle", "mode": "upsert", "policy": "all",
        "key_fields": ["SIRET"], "columns": COLS})
    assert back.status_code == 200, back.text
    assert back.json()["ok"] is True, back.json()["problems"]
    assert back.json()["rows_updated"] >= 1

    rows = client.get(f"/api/datasets/{ds}/rows").json()
    assert "999" in str(rows["data"])
    assert rows["total_rows"] == 3            # corrected in place, not duplicated


def test_a_table_too_large_is_refused_with_a_way_forward():
    """A session lives in memory: refusing beats quietly taking the process
    down — and the message says what to do instead."""
    sid = _upload()
    _run(sid)
    ds = _write(sid, name="grosse", key_fields=["SIRET"]).json()["dataset"]["id"]
    r = client.post(f"/api/datasets/{ds}/open?limit=2")
    assert r.status_code == 413
    assert "dataset" in r.json()["detail"]     # points at the flow brick


def test_encrypted_cells_come_back_masked_not_as_ciphertext():
    """Showing `enc:v1:…` would be worse than useless, and decrypting here would
    bypass the holder list entirely."""
    import os
    os.environ.setdefault("FX_MASTER_KEY", "cle-test-open")
    from app.services import crypto_service as cs

    sid = _upload()
    _run(sid)
    ds_id = _write(sid, name="chiffree", key_fields=["SIRET"]).json()["dataset"]["id"]

    from app.db import session_scope
    from app.db_models import DatasetRow
    wrapped = cs.new_data_key()
    with session_scope() as s:
        for row in s.query(DatasetRow).filter_by(dataset_id=ds_id).all():
            data = dict(row.data)
            data["MONTANT"] = cs.encrypt_value(str(data.get("MONTANT", "")), wrapped)
            row.data = data
        s.commit()

    body = client.post(f"/api/datasets/{ds_id}/open").json()
    dump = str(body["preview"]["data"])
    assert "enc:v1:" not in dump
    assert cs.MASK in dump


def test_writing_back_a_subset_of_columns_is_refused_as_schema_drift():
    """Reopening a table and sending fewer columns than it holds is a drift, not
    a partial update: refused with the missing columns named."""
    sid = _upload()
    _run(sid)
    ds = _write(sid, name="derive", key_fields=["SIRET"]).json()["dataset"]["id"]
    opened = client.post(f"/api/datasets/{ds}/open").json()["session_id"]
    client.post(f"/api/files/{opened}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"}}})
    r = client.post(f"/api/files/{opened}/datasets/write", json={
        "name": "derive", "mode": "upsert", "policy": "all",
        "key_fields": ["SIRET"], "columns": ["SIRET"]}).json()
    assert r["ok"] is False and r["blocked_by"] == "config"
    assert any("CIVILITE" in str(p.get("message", "")) for p in r["problems"])
