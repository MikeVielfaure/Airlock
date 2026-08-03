"""
source_recipe.py
─────────────────
Resolve a saved "source" artefact's recipe (dataset / external_db / api)
into a DataFrame — the server-side counterpart of attaching a source
interactively (`main.py`'s `attach_dataset_source`/`attach_external_db_source`/
`attach_api_source`), reused here so a flow can attach the same recipe
automatically at run time. Built on the same pure, HTTP-agnostic functions
those routes already call (`connections.parse_connection`, `external_db.run_query`,
`api_source.call`) — never a second way of resolving a connection.

Declared-schema validation is deliberately skipped here: it exists to confirm
an interactive attachment to a human, not to gate whether a flow's join can
run.
"""
from __future__ import annotations

import json

import pandas as pd
from sqlalchemy.orm import Session

from app import repository as repo
from app.services import api_source, connections, external_db
from app.services.dataset_frame_service import load_dataset_frame
from app.services.file_service import FileService

MAX_ROWS = 200_000  # matches the cap on any other attached source
_files = FileService()


def build_source_frame(s: Session, body: dict, environment: str,
                       _chain: frozenset = frozenset()) -> pd.DataFrame:
    """`body` is a "source" artefact version's stored recipe. Raises
    `repository.NotFound` (dataset), `ValueError` (bad/missing connection),
    `external_db.QueryTooLarge`, or `api_source.ApiCallError` as-is — the
    caller (a flow run) already knows how to turn any exception into a
    failed stage.

    `_chain` (flow ids already on the current resolution stack) only matters
    for `source_kind == "flow"` — threaded through by `store_service.resolve_flow`,
    never set by an interactive attach (which always starts a fresh chain)."""
    source_kind = body.get("source_kind")

    if source_kind == "dataset":
        return load_dataset_frame(s, body["dataset_id"])

    if source_kind == "external_db":
        values = repo.resolve_variables(s, environment=environment)
        kinds = repo.resolve_variable_kinds(s, environment=environment)
        conn = connections.parse_connection(
            body["connection"], values.get(body["connection"]),
            kinds.get(body["connection"]), "external_db")
        url = conn.get("url")
        if not url:
            raise ValueError(f"la connexion « {body['connection']} » n'a pas d'URL.")
        return external_db.run_query(url, body["query"], body.get("params") or {}, MAX_ROWS)

    if source_kind == "api":
        values = repo.resolve_variables(s, environment=environment)
        kinds = repo.resolve_variable_kinds(s, environment=environment)
        conn = connections.parse_connection(
            body["connection"], values.get(body["connection"]),
            kinds.get(body["connection"]), "api")
        base = (conn.get("base_url") or "").rstrip("/")
        rel = (body.get("path") or "").lstrip("/")
        url = f"{base}/{rel}" if rel else base
        if not url:
            raise ValueError(f"la connexion « {body['connection']} » n'a pas de base_url.")
        headers = {}
        if conn.get("token"):
            headers[conn.get("auth_header") or "Authorization"] = conn["token"]

        raw, _status = api_source.call(url, str(body.get("method", "GET")).upper(),
                                       headers, body.get("body"), 20.0)

        response_kind = body.get("response_kind", "json")
        if response_kind == "xlsx":
            return _files.load_xlsx_raw(raw, sheet=0)
        if response_kind == "csv":
            df, _enc, _delim = _files.load_csv_raw(raw, "AUTO", None)
            return df

        payload = json.loads(raw)
        payload = api_source.walk_json_path(payload, body.get("data_path", ""))
        if payload is None:
            payload = []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise api_source.ApiCallError("La réponse JSON n'est ni une liste ni un objet.")
        rows = [r for r in payload if isinstance(r, dict)]
        return pd.DataFrame(rows).astype("string").fillna("") if rows else pd.DataFrame()

    if source_kind == "flow":
        # A flow is already the same lazily-resolved "object" the other
        # three recipes are — its value is whatever running it produces
        # right now, never a session kept open. It needs its own fixed
        # input (source_dataset_id): nothing here can upload a file on its
        # behalf, so a flow without one cannot serve as a source.
        from app.services import store_service  # local: store_service imports this module
        flow_id = body["flow_id"]
        flow = repo.get_flow(s, flow_id)
        if not flow.source_dataset_id:
            raise ValueError(f"le flux « {flow.name} » n'a pas de source fixe (table interne) — "
                             f"il ne peut pas servir de source à un autre flux.")
        ds = repo.get_dataset(s, flow.source_dataset_id)
        inner_source_df = load_dataset_frame(s, flow.source_dataset_id)
        from app.main import _apply_filters
        _run, res = store_service.run_flow(
            s, flow, raw=None, source_name=f"{ds.name} ({inner_source_df.shape[0]} lignes) — source imbriquée",
            apply_filters=_apply_filters, source_df=inner_source_df, _chain=_chain)
        if not res.ok:
            raise ValueError(f"le flux source « {flow.name} » a échoué à l'étape « {res.stage} ».")
        return res.df if res.df is not None else pd.DataFrame()

    raise ValueError(f"type de source inconnu « {source_kind} »")
