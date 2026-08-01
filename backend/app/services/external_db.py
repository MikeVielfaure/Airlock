"""
A parameterised, read-only query against an external database — bound
through SQLAlchemy, never spliced into the SQL text, so a value coming from
anywhere upstream can never turn into an injection. The one engine behind
both the `external_db` flow brick and an attached session source: two
callers, never two ways of talking to a database.
"""
from __future__ import annotations

import pandas as pd
import sqlalchemy


class QueryTooLarge(Exception):
    """The query answered with more than `max_rows` rows."""


def run_query(url: str, query: str, params: dict, max_rows: int) -> pd.DataFrame:
    """Every value comes back as a string, `""` for NULL — the shape every
    downstream consumer (a flow brick's records, an attached source's
    DataFrame) already expects. Raises `QueryTooLarge` beyond `max_rows`;
    anything else (bad DSN, bad SQL) is left to bubble up as-is."""
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as c:
            result = c.execute(sqlalchemy.text(query), params)
            cols = list(result.keys())
            rows = [dict(zip(cols, r)) for r in result.fetchmany(max_rows + 1)]
    finally:
        engine.dispose()
    if len(rows) > max_rows:
        raise QueryTooLarge(f"la requête renvoie plus de {max_rows} lignes — "
                            f"ajoutez une LIMIT ou affinez les params.")
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows])
