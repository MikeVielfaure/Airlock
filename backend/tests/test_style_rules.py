"""
Conditional formatting: a rule describes how an existing column should
*look* — never what it holds. Evaluated the same two ways cross-source
computation already is (a plain `STYLE()` expression, or a DuckDB query for
several sources), kept entirely apart from the data itself.
"""
import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.compute_service import ComputeService
from app.services.style_rules import run_style_rules

client = TestClient(app)


# ── the engine, directly ─────────────────────────────────────────────────
def _frame():
    return pd.DataFrame({"nom": ["Alice", "Bob", "Chloe"], "age": ["17", "34", "12"]})


def test_style_produces_the_expected_token():
    val = ComputeService().evaluate(_frame(), 'STYLE("orange", "1", "")')
    assert list(val) == ["color:orange;bold:1;italic:0"] * 3


def test_style_with_no_args_is_a_harmless_empty_token():
    val = ComputeService().evaluate(_frame(), "STYLE()")
    assert list(val)[0] == "color:;bold:0;italic:0"


def test_a_plain_conditional_rule_only_styles_matching_rows():
    df = _frame()
    styles, errors = run_style_rules(
        df, {}, [("age", 'IF([age] < "18", STYLE("orange", "1"), STYLE())')])
    assert not errors
    tokens = list(styles["age"])
    assert tokens[0].startswith("color:orange")   # Alice, 17
    assert tokens[1] == "color:;bold:0;italic:0"  # Bob, 34 — no override
    assert tokens[2].startswith("color:orange")   # Chloe, 12


def test_an_unknown_target_column_is_refused_by_name():
    df = _frame()
    _styles, errors = run_style_rules(df, {}, [("taille", 'STYLE("red")')])
    assert "taille" in errors
    assert "n'existe pas" in errors["taille"]


def test_an_empty_expression_is_refused():
    df = _frame()
    _styles, errors = run_style_rules(df, {}, [("age", "   ")])
    assert "age" in errors


def test_a_cross_source_rule_returns_a_bare_color_per_row():
    df = _frame()
    ref = pd.DataFrame({"nom": ["Alice", "Bob", "Chloe"], "risque": ["eleve", "faible", "eleve"]})
    styles, errors = run_style_rules(
        df, {"ref": ref},
        [("nom", "SELECT self._row_id, "
                 "CASE WHEN ref.risque = 'eleve' THEN 'red' ELSE '' END AS couleur "
                 "FROM self LEFT JOIN ref ON self.nom = ref.nom")])
    assert not errors
    assert list(styles["nom"]) == ["red", "", "red"]


def test_a_cross_source_rule_returning_more_than_one_column_is_refused():
    df = _frame()
    ref = pd.DataFrame({"nom": ["Alice"], "a": ["1"], "b": ["2"]})
    _styles, errors = run_style_rules(
        df, {"ref": ref},
        [("nom", "SELECT self._row_id, ref.a, ref.b FROM self LEFT JOIN ref ON self.nom = ref.nom")])
    assert "nom" in errors
    assert "exactement une" in errors["nom"]


def test_a_sensitive_column_is_masked_before_a_plain_rule_sees_it():
    from app.services.crypto_service import MASK

    df = pd.DataFrame({"nom": ["Alice"], "salaire": ["90000"]})
    styles, errors = run_style_rules(
        df, {}, [("salaire", "STYLE([salaire], \"\", \"\")")], sensitive_cols=frozenset({"salaire"}))
    assert not errors
    assert MASK in list(styles["salaire"])[0]


def test_one_bad_rule_does_not_block_a_good_one():
    df = _frame()
    styles, errors = run_style_rules(
        df, {}, [("age", "SELECT * FROM nope_table"),
                ("nom", 'STYLE("blue")')])
    assert "age" in errors
    assert list(styles["nom"]) == ["color:blue;bold:0;italic:0"] * 3


# ── end-to-end via /process and /rows ────────────────────────────────────
CSV = "NOM;AGE\nAlice;17\nBob;34\nChloe;12\n"
FIELDS = {"NOM": {"name": ["NOM"], "type": "string"}, "AGE": {"name": ["AGE"], "type": "string"}}
COLS = ["NOM", "AGE"]


def _upload(csv: str = CSV):
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_process_returns_styles_aligned_with_data_and_status():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "style_rules": [{"column": "AGE", "expression":
            'IF([AGE] < "18", STYLE("orange", "1"), STYLE())'}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("style_errors")
    age_col = body["columns"].index("AGE")
    styles = body["styles"]
    assert len(styles) == len(body["data"])
    assert styles[0][age_col].startswith("color:orange")   # Alice, 17
    assert styles[1][age_col] == "color:;bold:0;italic:0"  # Bob, 34
    assert styles[2][age_col].startswith("color:orange")   # Chloe, 12
    # the status column is untouched by styling
    assert body["status"][0][age_col] in ("OK", "CLEANED")


def test_an_invalid_style_rule_reports_a_named_error_without_failing_the_run():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "style_rules": [{"column": "INCONNUE", "expression": 'STYLE("red")'}]})
    assert r.status_code == 200, r.text
    assert "INCONNUE" in r.json()["style_errors"]


def test_rows_reuses_the_cached_styles_without_recomputing():
    sid = _upload()
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "style_rules": [{"column": "AGE", "expression":
            'IF([AGE] < "18", STYLE("orange", "1"), STYLE())'}]})
    r = client.get(f"/api/files/{sid}/rows", params={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    age_col = body["columns"].index("AGE")
    assert body["styles"][0][age_col].startswith("color:orange")
    assert body["styles"][1][age_col] == "color:;bold:0;italic:0"
