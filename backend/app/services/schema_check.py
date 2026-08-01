"""
A declared schema on a connection point is a contract, not documentation: a
column the schema names but the response doesn't have, or a value that
doesn't match the declared type, refuses the source outright — the same
"a contract that finds nothing has verified nothing" principle already
applied to `require_columns` on the config brick. Reuses
`FunctionService.check_type_value`, the exact per-value type check a
regular file column already goes through — one engine, not a second one
for a query result.
"""
from __future__ import annotations

import pandas as pd

from app.services.function_service import FunctionService


def validate_against_schema(df: pd.DataFrame, schema_json: dict) -> None:
    """Raises `ValueError` naming exactly what's wrong. A schema with no
    declared columns is a no-op — nothing was promised, nothing to check."""
    columns = schema_json.get("columns") or []
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"colonne(s) absente(s) de la réponse : {', '.join(missing)}")

    types = schema_json.get("types") or {}
    fs = FunctionService()
    bad = [f"{col} (attendu {t})" for col, t in types.items()
           if col in df.columns and t != "string"
           and not df[col].map(lambda v: fs.check_type_value(t, v)).all()]
    if bad:
        raise ValueError(f"valeur(s) qui ne correspondent pas au type déclaré : {', '.join(bad)}")
