"""
Conditional cell formatting — a rule per target column, evaluated the same
two ways cross-source computation already is: a plain expression (single
row, `ComputeService`, the new `STYLE()` builtin) or a DuckDB query (several
sources, `duck_compute.run_sql_computed`) when the text starts with `SELECT`
or `WITH`. One rule shape either way — the split is by content, not by two
separate lists, because a formatting rule is lighter-weight than a full
computed column.

The result is never written into `df_post`: a style rule describes how an
*existing* column should look, never a new value, so it is kept in a
separate `{column: pd.Series}` map the caller merges into the response
alongside `status`, never into the data itself.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from app.services.crypto_service import MASK


def run_style_rules(df_post: pd.DataFrame, attached: Dict[str, pd.DataFrame],
                    rules: List[Tuple[str, str]],
                    sensitive_cols: frozenset = frozenset()
                    ) -> Tuple[Dict[str, pd.Series], Dict[str, str]]:
    """Run each `(column, expression)` rule and return a column -> per-row
    style-token Series map, plus one error message per failing column —
    never a raised exception, so one bad rule cannot block the others or
    the run itself."""
    styles: Dict[str, pd.Series] = {}
    errors: Dict[str, str] = {}

    for column, expression in rules:
        if column not in df_post.columns:
            errors[column] = f"la colonne « {column} » n'existe pas"
            continue
        text = (expression or "").strip()
        if not text:
            errors[column] = "condition vide"
            continue

        if text.lower().startswith(("select", "with")):
            from app.services.duck_compute import run_sql_computed

            probe, added, sub_errors = run_sql_computed(
                df_post.copy(), attached, [(column, text, "replace")],
                sensitive_cols=sensitive_cols)
            if sub_errors:
                errors[column] = next(iter(sub_errors.values()))
                continue
            if len(added) != 1:
                errors[column] = ("la requête doit renvoyer exactement une "
                                  "colonne de style, en plus de `_row_id`")
                continue
            styles[column] = probe[added[0]]
        else:
            from app.services.compute_service import ComputeError, ComputeService

            # A style rule is a direct visual signal — masking the obvious
            # field is not enough if a condition on the real value shows
            # through as a color instead, the same reasoning already
            # applied to cross-source SQL blocks.
            masked = df_post
            if sensitive_cols:
                masked = df_post.copy()
                for c in sensitive_cols:
                    if c in masked.columns:
                        masked[c] = MASK
            try:
                styles[column] = ComputeService().evaluate(masked, text)
            except ComputeError as e:
                errors[column] = str(e)

    return styles, errors
