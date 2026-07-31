"""
Cross-source SQL — the one capability `ComputeService` cannot offer.

`ComputeService.evaluate()` is deliberately mono-row/mono-table: every
expression sees exactly one frame's columns. That is correct for a per-cell
formula, but it structurally cannot join, window, or aggregate across
sources. Rather than bending the single expression engine into a second,
hidden meaning, cross-source work gets its own clearly-separated capability
here — reached from the same one place (`process_service.run_pipeline`)
every computed column already goes through.

The result must land as columns on the *existing* session rows (never a
replacement of the session), so a query must be row-preserving: every row it
returns is joined back by `_row_id`, the session's own pandas index exposed
as an ordinary column on a `self` view. A `GROUP BY` that drops `_row_id`
fails with a named error instead of silently truncating — that push is
deliberate: a per-row total wants a window function
(`COUNT(*) OVER (PARTITION BY ...)`), not a collapse.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import duckdb
import pandas as pd

from app.services.crypto_service import MASK

_ROW_ID = "_row_id"


def run_sql_computed(df_post: pd.DataFrame, attached: Dict[str, pd.DataFrame],
                     blocks: List[Tuple[str, str, str]],
                     sensitive_cols: frozenset = frozenset()
                     ) -> Tuple[pd.DataFrame, List[str], Dict[str, str]]:
    """Run each `(name, query, mode)` block against `self` (the session's own
    rows) plus every attached source, and merge the result back onto
    `df_post` by `_row_id`. Returns the (possibly widened) frame, the new
    column names, and one error message per failing block name — never a
    raised exception, so one bad block cannot take the whole run down.

    `mode` is either `"replace"` (overwrite the target column row by row —
    the only behaviour before this existed) or `"fill_empty"` (only a blank
    cell in `df_post` is ever written, so a value already there — say, an
    age filled in for one row but not another — is never clobbered by a
    stale or mismatched value from the source).

    Columns declared confidential (`sensitive_cols`) are masked in the `self`
    view before DuckDB ever sees them — masking the obvious field is not
    enough if the plaintext comes back through a derived column instead, and
    a query's text is free-form SQL, not the `[Col]` syntax the expression
    engine's own taint tracking knows how to scan."""
    new_cols: List[str] = []
    errors: Dict[str, str] = {}

    for name, query, mode in blocks:
        text = (query or "").strip()
        if not text:
            errors[name] = "requête vide"
            continue
        if not text.lower().startswith(("select", "with")):
            errors[name] = "la requête doit commencer par SELECT ou WITH"
            continue

        con = duckdb.connect(":memory:")
        try:
            # Fail closed: no filesystem, network, extension or external
            # ATTACH access — only the frames explicitly registered below.
            con.execute("SET enable_external_access=false")

            self_view = df_post.reset_index().rename(columns={"index": _ROW_ID})
            for col in sensitive_cols:
                if col in self_view.columns:
                    self_view[col] = MASK
            con.register("self", self_view)
            for src_name, frame in attached.items():
                con.register(src_name, frame)

            try:
                result = con.execute(text).df()
            except Exception as e:  # noqa: BLE001 — a bad query names its block
                errors[name] = f"{type(e).__name__}: {e}"
                continue

            if _ROW_ID not in result.columns:
                errors[name] = (f"la requête doit retourner `{_ROW_ID}` pour se "
                                f"rattacher aux lignes existantes — utilisez une "
                                f"fonction fenêtrée plutôt qu'un GROUP BY qui "
                                f"supprime cette colonne")
                continue

            result = result.set_index(_ROW_ID)
            result.index = result.index.astype(df_post.index.dtype, copy=False)
            if result.index.duplicated().any():
                errors[name] = (f"la requête renvoie plusieurs lignes pour le même "
                                f"`{_ROW_ID}` — utilisez une fonction fenêtrée ou "
                                f"dédupliquez la source attachée avant la jointure")
                continue
            added = [c for c in result.columns if c != _ROW_ID]
            if not added:
                errors[name] = "la requête ne retourne aucune colonne à ajouter"
                continue

            if mode == "fill_empty":
                missing = [c for c in added if c not in df_post.columns]
                if missing:
                    errors[name] = (f"le mode « compléter » exige que "
                                    f"{', '.join(missing)} existe déjà — utilisez "
                                    f"« remplacer » pour créer une nouvelle colonne")
                    continue
                for col in added:
                    incoming = result[col].reindex(df_post.index).fillna("").astype(str)
                    # `.astype(str)` on a nullable dtype turns a real NaN into
                    # the *text* "<NA>", not "" — fillna() first or a blank
                    # cell is missed entirely, exactly the bug this mode
                    # exists to avoid.
                    blank = df_post[col].fillna("").astype(str).str.strip() == ""
                    fillable = blank & (incoming.str.strip() != "")
                    df_post.loc[fillable, col] = incoming[fillable]
                new_cols.extend(added)
            else:
                for col in added:
                    df_post[col] = result[col].reindex(df_post.index)
                    df_post[col] = df_post[col].fillna("").astype(str)
                new_cols.extend(added)
        finally:
            con.close()

    return df_post, new_cols, errors
