"""
Cross-source SQL (DuckDB) — the one thing the single-frame expression engine
cannot do: join, window or aggregate against another frame. A narrowly
scoped, clearly separate capability — never a second meaning bolted onto
`ComputeService`.
"""
import pandas as pd

from app.services.crypto_service import MASK
from app.services.duck_compute import run_sql_computed


def _session_frame():
    return pd.DataFrame({"client": ["ACME", "BETA", "GAMMA"], "montant": ["100", "80", "50"]})


def test_a_join_against_an_attached_source_lands_as_a_column():
    df = _session_frame()
    ref = pd.DataFrame({"client": ["ACME", "BETA", "GAMMA"],
                        "secteur": ["industrie", "services", "industrie"]})
    out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("secteur", "SELECT self._row_id, ref.secteur FROM self "
                     "LEFT JOIN ref ON self.client = ref.client", "replace")])
    assert not errors
    assert cols == ["secteur"]
    assert list(out["secteur"]) == ["industrie", "services", "industrie"]


def test_a_window_function_gives_a_per_row_total_without_collapsing_rows():
    df = _session_frame()
    out, cols, errors = run_sql_computed(
        df, {},
        [("rang", "SELECT _row_id, RANK() OVER (ORDER BY CAST(montant AS INTEGER) DESC) "
                 "AS rang FROM self", "replace")])
    assert not errors
    assert len(out) == 3          # rows preserved — the whole point
    assert list(out["rang"]) == ["1", "2", "3"]   # every cell is stringified, like the rest of the pipeline


def test_a_query_without_row_id_is_refused_with_a_named_reason():
    df = _session_frame()
    out, cols, errors = run_sql_computed(
        df, {}, [("total", "SELECT client, COUNT(*) AS n FROM self GROUP BY client", "replace")])
    assert cols == []
    assert "total" in errors
    assert "_row_id" in errors["total"]


def test_a_query_not_starting_with_select_or_with_is_refused():
    df = _session_frame()
    _out, cols, errors = run_sql_computed(df, {}, [("x", "DELETE FROM self", "replace")])
    assert cols == []
    assert "x" in errors
    assert "SELECT" in errors["x"]


def test_filesystem_access_is_blocked():
    df = _session_frame()
    _out, cols, errors = run_sql_computed(
        df, {}, [("x", "SELECT * FROM read_csv_auto('C:/Windows/win.ini')", "replace")])
    assert cols == []
    assert "x" in errors    # blocked one way or another — never actually reads the file


def test_duplicate_row_id_in_the_result_is_refused():
    df = _session_frame()
    ref = pd.DataFrame({"client": ["ACME", "ACME"], "libelle": ["a", "b"]})
    _out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("libelle", "SELECT self._row_id, ref.libelle FROM self "
                     "JOIN ref ON self.client = ref.client", "replace")])
    assert cols == []
    assert "libelle" in errors
    assert "plusieurs lignes" in errors["libelle"]


def test_a_declared_sensitive_column_is_masked_before_duckdb_sees_it():
    df = pd.DataFrame({"client": ["ACME"], "salaire": ["50000"]})
    # The block's own name ("salaire_vu") is just its label — the output
    # column comes from the SELECT itself, aliased or not.
    out, cols, errors = run_sql_computed(
        df, {}, [("salaire_vu", "SELECT _row_id, salaire AS salaire_recu FROM self", "replace")],
        sensitive_cols=frozenset({"salaire"}))
    assert not errors
    assert cols == ["salaire_recu"]
    assert out["salaire_recu"].iloc[0] == MASK


def test_an_empty_query_is_refused():
    df = _session_frame()
    _out, cols, errors = run_sql_computed(df, {}, [("vide", "   ", "replace")])
    assert cols == []
    assert "vide" in errors


def test_one_bad_block_does_not_take_down_a_good_one():
    df = _session_frame()
    out, cols, errors = run_sql_computed(
        df, {},
        [("casse", "SELECT * FROM nope_table", "replace"),
         ("ok", "SELECT _row_id, UPPER(client) AS client_maj FROM self", "replace")])
    assert "casse" in errors
    assert cols == ["client_maj"]
    assert list(out["client_maj"]) == ["ACME", "BETA", "GAMMA"]


# ── fill_empty mode: complete a gap without ever overwriting a real value ──
def _partial_frame():
    return pd.DataFrame({
        "nom": ["Dupont", "Martin", "Durand"],
        "prenom": ["Alice", "Bob", "Chloe"],
        "age": ["34", "", ""],           # Martin and Durand are missing an age
    })


def _reference():
    return pd.DataFrame({
        "nom": ["Dupont", "Martin", "Durand"],
        "prenom": ["Alice", "Bob", "Chloe"],
        "age": ["99", "41", "27"],       # deliberately wrong for Dupont
    })


def test_fill_empty_only_touches_blank_cells():
    df = _partial_frame()
    ref = _reference()
    out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("age", "SELECT self._row_id, ref.age FROM self "
                 "LEFT JOIN ref ON self.nom = ref.nom AND self.prenom = ref.prenom",
          "fill_empty")])
    assert not errors
    assert cols == ["age"]
    assert list(out["age"]) == ["34", "41", "27"]   # Dupont's real 34 survives


def test_fill_empty_on_an_unknown_column_is_refused():
    df = _partial_frame()
    ref = _reference()
    out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("taille", "SELECT self._row_id, ref.age AS taille FROM self "
                    "LEFT JOIN ref ON self.nom = ref.nom", "fill_empty")])
    assert cols == []
    assert "taille" in errors
    assert "existe déjà" in errors["taille"]


def test_fill_empty_recognises_a_real_nan_as_blank_not_just_empty_string():
    """`.astype(str)` on a nullable dtype turns NaN into the text "<NA>" —
    exactly what a CSV upload with a truly empty cell produces. Missing that
    would silently defeat the whole point of this mode."""
    df = pd.DataFrame({
        "nom": ["Dupont", "Martin"], "prenom": ["Alice", "Bob"],
        "age": pd.array(["34", None], dtype="string"),
    })
    ref = pd.DataFrame({"nom": ["Dupont", "Martin"], "prenom": ["Alice", "Bob"],
                        "age": [99, 41]})
    out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("age", "SELECT self._row_id, ref.age FROM self "
                 "LEFT JOIN ref ON self.nom = ref.nom AND self.prenom = ref.prenom",
          "fill_empty")])
    assert not errors
    assert list(out["age"]) == ["34", "41"]


def test_fill_empty_leaves_unmatched_rows_blank():
    df = _partial_frame()
    ref = pd.DataFrame({"nom": ["Martin"], "prenom": ["Bob"], "age": ["41"]})
    out, cols, errors = run_sql_computed(
        df, {"ref": ref},
        [("age", "SELECT self._row_id, ref.age FROM self "
                 "LEFT JOIN ref ON self.nom = ref.nom AND self.prenom = ref.prenom",
          "fill_empty")])
    assert not errors
    assert list(out["age"]) == ["34", "41", ""]   # Durand had no match — stays blank
