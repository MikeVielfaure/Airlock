"""
Cross-source SQL end-to-end: attach a source, write a SQL block, run
validation — the resulting column shows up exactly like an ordinary computed
column, because from Schéma & Règles / Rapport's point of view, it is one.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "CODE;LIBELLE\nFR;france-brut\nBE;belgique-brut\n"
FIELDS = {"CODE": {"name": ["CODE"], "type": "string", "identifiant": True},
          "LIBELLE": {"name": ["LIBELLE"], "type": "string"}}
COLS = ["CODE", "LIBELLE"]


def _upload(csv: str = CSV):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_a_sql_block_joins_an_uploaded_source_and_appears_as_a_computed_column():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/sources/upload", data={"name": "traduction"},
                    files={"file": ("t.csv", io.BytesIO(b"CODE;NOM\nFR;France\nBE;Belgique\n"), "text/csv")})
    assert r.status_code == 200, r.text

    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "sql_computed": [{"name": "nom_pays", "expression":
            "SELECT self._row_id, traduction.NOM AS nom_pays "
            "FROM self LEFT JOIN traduction ON self.CODE = traduction.CODE"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "nom_pays" in body["computed"]
    assert not body["compute_errors"]
    assert "France" in str(body["data"]) and "Belgique" in str(body["data"])
    assert "nom_pays" in body["columns"]


def test_an_invalid_sql_block_reports_a_named_error_without_failing_the_run():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "sql_computed": [{"name": "casse", "expression": "SELECT * FROM nope_table"}]})
    assert r.status_code == 200, r.text     # the run itself still succeeds
    assert "casse" in r.json()["compute_errors"]


def test_a_sql_block_result_is_available_to_a_plain_expression_afterwards():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "sql_computed": [{"name": "code_lower", "expression":
            "SELECT _row_id, LOWER(CODE) AS code_lower FROM self"}],
        "computed": [{"name": "libelle_complet",
                     "expression": 'CONCAT([code_lower], "-", [LIBELLE])'}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["compute_errors"]
    assert "fr-france-brut" in str(body["data"]).lower()


def test_fill_empty_completes_a_gap_without_overwriting_existing_values():
    """The nom/prénom/âge case: age is known for one row and missing for
    another; completing from a source must never clobber the row that
    already had a real value."""
    csv = "NOM;PRENOM;AGE\nDupont;Alice;34\nMartin;Bob;\n"
    sid = _upload(csv)
    r = client.post(f"/api/files/{sid}/sources/upload", data={"name": "annuaire"},
                    files={"file": ("a.csv", io.BytesIO(
                        b"NOM;PRENOM;AGE\nDupont;Alice;99\nMartin;Bob;41\n"), "text/csv")})
    assert r.status_code == 200, r.text

    fields = {"NOM": {"name": ["NOM"], "type": "string", "identifiant": True},
             "PRENOM": {"name": ["PRENOM"], "type": "string"},
             "AGE": {"name": ["AGE"], "type": "string"}}
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM", "PRENOM", "AGE"], "fields": fields,
        "sql_computed": [{"name": "age", "mode": "fill_empty", "expression":
            "SELECT self._row_id, annuaire.AGE FROM self "
            "LEFT JOIN annuaire ON self.NOM = annuaire.NOM AND self.PRENOM = annuaire.PRENOM"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["compute_errors"]
    age_col = body["columns"].index("AGE")
    ages = [row[age_col] for row in body["data"]]
    assert ages == ["34", "41"]   # Dupont's real age survives, Martin's gap is filled


def test_a_sql_block_leaves_a_reproducible_history_entry():
    sid = _upload()
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "sql_computed": [{"name": "code_lower", "expression":
            "SELECT _row_id, LOWER(CODE) AS code_lower FROM self"}]})
    from app.db import session_scope
    from app.session import store
    with session_scope() as s:
        hist = store.get(s, sid).history
    ops = {h["op"]: h for h in hist}
    assert "sql_compute" in ops
    assert ops["sql_compute"]["columns"] == ["code_lower"]
