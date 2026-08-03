"""
tco_target_service.py
──────────────────────
A TCO's TARGET_LABEL is free text by default — whoever completes a
correspondence can type anything. For a canonical vocabulary (a job-code
list, a legal-structure list) that is exactly the gap: nothing stops a typo,
or a value nobody actually agreed on, from entering the table.

A `target_source` closes it: one per TYPE, `{dataset_id, query}`. `query` is
a DuckDB SELECT run against the referenced dataset's rows (registered under
the fixed alias `list`) and must return a column named `value` — reusing the
same sandboxed-DuckDB-over-a-registered-frame mechanism `duck_compute.py`
already runs cross-source computed columns through, rather than a second,
narrower filter-only mechanism that could only ever express "column equals
one fixed value". A plain `SELECT code AS value FROM list WHERE type='job'`
covers the simple case; a join, a CASE, a UNION of two lists — anything
DuckDB can express — covers the rest, for free.
"""
from __future__ import annotations

import duckdb
from sqlalchemy.orm import Session

from app import repository as repo
from app.services.dataset_frame_service import load_dataset_frame


class BadTargetSource(Exception):
    pass


def resolve_allowed_values(s: Session, dataset_id: str, query: str) -> list[str]:
    """Run `query` against the dataset's rows and return the distinct,
    non-empty values of its `value` column, sorted."""
    try:
        list_df = load_dataset_frame(s, dataset_id)
    except repo.NotFound as e:
        raise BadTargetSource(str(e))
    text = (query or "").strip()
    if not text:
        raise BadTargetSource("La requête est vide.")
    if not text.lower().startswith(("select", "with")):
        raise BadTargetSource("La requête doit commencer par SELECT ou WITH.")
    con = duckdb.connect(":memory:")
    try:
        # Fail closed: no filesystem, network, extension or external ATTACH
        # access — only the one frame registered below.
        con.execute("SET enable_external_access=false")
        con.register("list", list_df)
        try:
            result = con.execute(text).df()
        except Exception as e:  # noqa: BLE001 — a bad query is reported, not raised
            raise BadTargetSource(f"{type(e).__name__}: {e}")
        if "value" not in result.columns:
            raise BadTargetSource(
                "La requête doit retourner une colonne nommée `value` "
                f"(colonnes trouvées : {', '.join(result.columns) or '(aucune)'}).")
        return sorted({str(v) for v in result["value"].dropna().tolist() if str(v).strip()})
    finally:
        con.close()
