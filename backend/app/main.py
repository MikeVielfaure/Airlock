"""
main.py
───────
HTTP layer. Thin by design: every route validates input, calls a service,
serializes the result. No business logic lives here.

Flow mirrors the original tool:
  upload → (optional) header treatment → configure fields → process → report
with YAML import/export and a TCO lookup table available throughout.
"""

from typing import Optional

import io
import json
import base64
import logging
import os
import time

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.models import (
    AddRowsRequest, BlankSessionRequest, CellEdit, DeleteRowsRequest, EditCellsRequest, EditCellsResponse,
    RowsMutationResponse,
    ExportRequest, ExportResponse, ExpressionCheck, ExpressionResult,
    FieldConfig, FileResponse, HeaderRequest, ImportRequest, ImportResponse,
    MatchInfo, Presets, ProcessRequest, ProcessResponse, ProcessStats, RowsResponse, TablePreview, TcoResponse,
    PipelineResponse, SourceInfo, AttachDatasetSource, AttachSessionSource, ReorderRowRequest,
    AttachExternalDbSource, AttachApiSource, AttachFlowSource, SetSourceKey,
    ExternalDbSessionRequest, ApiSessionRequest, DiffRequest,
)
from app.services.config_service import ConfigService
from app.services.file_service import FileService
from app.services import crypto_service as _crypto
from app.services import dataset_service as _ds
from app.services import auth_service as _auth
from app.services.function_service import (
    REGEX_PRESETS, UI_DATE_FORMATS, FunctionService,
)
from app.services.process_service import ProcessService
from app.services.tco_service import TcoService
from app.services import diff_service as _diff
from app.services import xlsx_style
from app.session import store
from app.db import commit, get_session, init_db, session_scope
from app.logging_setup import configure_logging
from sqlalchemy.orm import Session as DbSession
from pydantic import BaseModel
from app.flow_graph import FlowGraph
from app import repository as repo
from app.store_routes import router as store_router
from app.edi_routes import router as edi_router
from app.dataset_routes import router as dataset_router
from app.mapping_routes import router as mapping_router
from app.graph_routes import router as graph_router
from app.ops_routes import router as ops_router
from app.env_routes import router as env_router
from app.auth_routes import router as auth_router, admin_router
from app.crypto_routes import router as crypto_router
from app.load_routes import router as load_router
from app.run_routes import router as run_router
from app.auth_routes import require_capability, require_user
from app.services.pipeline_engine import PipelineEngine

from contextlib import asynccontextmanager, contextmanager


@asynccontextmanager
async def _lifespan(app):
    init_db()                          # bring the artefact store schema to head
    yield

configure_logging()
_access_log = logging.getLogger("app.access")

app = FastAPI(title="File Explorer API", version="3.0.0", lifespan=_lifespan)
app.include_router(store_router)    # artefact library, flows, runs (Postgres/SQLite)
app.include_router(edi_router)      # EDI: inspect, validate, pivot, generate, convert, models
app.include_router(dataset_router)  # datasets: preflight, write, read back, write log
app.include_router(mapping_router)
app.include_router(graph_router)
app.include_router(auth_router)       # identity: internal accounts + SSO, one account either way
app.include_router(admin_router)
app.include_router(crypto_router)
app.include_router(load_router)
app.include_router(run_router)        # flows served at a readable address, with OpenAPI       # load a file into an existing target (tco, table)     # confidential columns: keys, holders, reveal trail      # delegated administration: members, roles, providers
app.include_router(env_router)        # environment profiles: modules, pinned config, actions
app.include_router(ops_router)        # connection points + the operations table      # visual flows: bricks wired into a graph, callable as an API   # pivot central: to-object, convert anything<->anything, suggest

_MAX_UPLOAD_BYTES = int(os.environ.get("FX_MAX_UPLOAD_MB", "200")) * 1024 * 1024


class _MaxBodySizeMiddleware:
    """
    Refuse early on Content-Length rather than let pandas load an enormous
    file into memory. This is the backend's own line of defense — the
    reverse proxy in front (Caddy in `docker-compose.prod.yml`) refuses a
    request far earlier still, before these bytes ever reach this
    container, including a chunked body with no Content-Length that this
    check alone cannot see coming.
    """
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for k, v in scope.get("headers", []):
                if k == b"content-length" and int(v) > self.max_bytes:
                    resp = Response(
                        f"Fichier trop volumineux (max {self.max_bytes // (1024 * 1024)} Mo).",
                        status_code=413)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# Added before CORSMiddleware so CORS ends up outermost (Starlette wraps
# the LAST-added middleware around all the others) — a 413 from this check
# must still carry CORS headers, or a cross-origin browser call sees an
# opaque CORS failure instead of the real, readable error.
app.add_middleware(_MaxBodySizeMiddleware, max_bytes=_MAX_UPLOAD_BYTES)

def _parse_cors_origins(raw: str) -> list[str]:
    """Empty/unset stays `["*"]` so local dev keeps working unchanged; set
    FX_CORS_ORIGINS to the real domain(s), comma-separated, once one exists."""
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(os.environ.get("FX_CORS_ORIGINS", "*")),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """One structured line per request — method, path, status, duration.
    An unhandled exception is logged with its traceback here too, then
    re-raised so FastAPI's own error handling still produces the response."""
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _access_log.exception("unhandled exception", extra={
            "method": request.method, "path": request.url.path,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        })
        raise
    _access_log.info("request", extra={
        "method": request.method, "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": int((time.perf_counter() - start) * 1000),
    })
    return response

_files   = FileService()
_process = ProcessService()
_config  = ConfigService()
_tco     = TcoService()
_engine  = PipelineEngine(files=_files, process=_process, config=_config, tco=_tco)

_AUTO = "AUTO"
_REPORT_CAP = 5000               # rows returned in the report payload


# ──────────────────────────────────────────────────────────────
# SERIALIZATION HELPERS
# ──────────────────────────────────────────────────────────────

def _cell(v) -> str:
    """Stringify a cell, mapping every flavour of empty to ''."""
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    s = str(v)
    return "" if s in ("nan", "NaT", "None", "<NA>") else s


def _preview(df: pd.DataFrame, limit: int) -> TablePreview:
    head = df.head(limit)
    return TablePreview(
        columns=[str(c) for c in df.columns],
        data=[[_cell(v) for v in row] for row in head.itertuples(index=False, name=None)],
        total_rows=int(len(df)),
        shown_rows=int(len(head)),
        index=[int(i) for i in head.index],
    )


def _display_columns(visible_cols, fields, df_post) -> list[str]:
    """
    Resolve visible columns to the names they actually have in df_post,
    preserving order. A rename may have been skipped (e.g. name collision),
    so we trust df_post rather than assuming the rename happened.

    `col` is the field's *key* in visible_cols — not necessarily the file's
    actual column name (a manually declared field, or one whose `name` lists
    alternates like ["job", "Poste"] while staying keyed "job"). Falling back
    to `fc.name` mirrors `_find_column`'s own resolution, so a field that
    genuinely got processed under an alternate name is not dropped from the
    output just because its key never matched anything literally.
    """
    out, seen = [], set()
    for col in visible_cols:
        fc = fields.get(col)
        wants_rename = bool(fc and fc.mapping and getattr(fc, "rename_output", True) and fc.mapping != col)
        found_name = next((n for n in (fc.name or []) if n in df_post.columns), None) if fc else None
        if col in df_post.columns:
            final = col                                   # rename skipped or none
        elif wants_rename and fc.mapping in df_post.columns:
            final = fc.mapping                            # rename was applied
        elif found_name:
            final = found_name                            # keyed differently from the file's header
        else:
            continue
        if final not in seen:
            out.append(final)
            seen.add(final)
    return out


def _filter_mask(series: "pd.Series", expr: str) -> "pd.Series":
    """
    Boolean mask for a column filter expression. Operators (case-insensitive):
      • text            contains (default)
      • =text           equals exactly
      • !=text          not equal
      • !text           does NOT contain
      • in:a,b,c        value is one of the list
      • !in:a,b,c       value is none of the list
      • >n  <n  >=n  <=n numeric comparison
      • empty / blank   cell is empty/null   (!empty = non-empty)
    Shared by /rows, /export and /pipeline so they always agree.
    """
    s = str(expr).strip()
    sv = series.map(lambda v: "" if pd.isna(v) else str(v))          # null-aware string view
    if s == "":
        return pd.Series(True, index=series.index)
    low = sv.str.lower()
    sl = s.lower()

    if sl in ("empty", "blank", "null", "isnull", "is null", "(empty)"):
        return sv.str.strip() == ""
    if sl in ("!empty", "!blank", "!null", "notempty", "notblank", "notnull",
              "not null", "not empty", "not blank", "isnotnull"):
        return sv.str.strip() != ""

    for op in (">=", "<=", ">", "<"):
        if s.startswith(op):
            try:
                num = float(s[len(op):].strip().replace(",", "."))
            except ValueError:
                break
            col_num = pd.to_numeric(sv.str.replace(",", ".", regex=False), errors="coerce")
            cmp = {">=": col_num >= num, "<=": col_num <= num,
                   ">": col_num > num, "<": col_num < num}[op]
            return cmp.fillna(False)

    if sl.startswith("!in:"):
        items = [x.strip().lower() for x in s[4:].split(",") if x.strip()]
        return ~low.isin(items)
    if sl.startswith("in:"):
        items = [x.strip().lower() for x in s[3:].split(",") if x.strip()]
        return low.isin(items)
    if s.startswith("!="):
        return low != s[2:].strip().lower()
    if s.startswith("="):
        return low == s[1:].strip().lower()
    if s.startswith("!"):
        return ~low.str.contains(s[1:].strip().lower(), regex=False, na=False)
    return low.str.contains(sl, regex=False, na=False)


def _split_group(raw: str) -> tuple[str | None, str]:
    """
    A filter may be prefixed with a group tag `:<id>` (e.g. `:1!empty`).
    Returns (group_id, expression). Filters sharing a group are OR-ed together;
    ungrouped filters (and distinct groups) are AND-ed. `group_id` is None when
    the filter has no tag.
    """
    s = str(raw)
    if s.startswith(":") and len(s) > 1 and (s[1].isalnum()):
        i = 1
        while i < len(s) and s[i].isalnum():
            i += 1
        return s[1:i], s[i:].strip()
    return None, s


def _apply_filters(df: "pd.DataFrame", fmap: dict) -> "pd.DataFrame":
    """
    Apply column filters with AND/OR grouping. Ungrouped filters must all match
    (AND). Filters tagged with the same `:<id>` are combined with OR; each group
    is then AND-ed with the rest. All masks are built on the original frame so
    they stay index-aligned.
    """
    if not fmap:
        return df
    and_masks: list = []
    groups: dict[str, list] = {}
    for col, raw in fmap.items():
        if col not in df.columns or not str(raw).strip():
            continue
        gid, expr = _split_group(raw)
        if expr.strip() == "":
            continue
        mask = _filter_mask(df[col], expr)
        if gid is None:
            and_masks.append(mask)
        else:
            groups.setdefault(gid, []).append(mask)
    final = pd.Series(True, index=df.index)
    for m in and_masks:
        final &= m
    for masks in groups.values():
        gmask = pd.Series(False, index=df.index)
        for m in masks:
            gmask |= m
        final &= gmask
    return df[final]


def _status_of(result: str, was_clean: bool) -> str:
    if result.startswith("MAPPING OK"):
        return "MAPPING_OK"
    if result.startswith("MAPPING KO"):
        return "MAPPING_KO"
    if result == "NO_TCO":
        return "NO_TCO"
    if result != "OK":
        return "ERROR"
    return "CLEANED" if was_clean else "OK"


# ──────────────────────────────────────────────────────────────
# META
# ──────────────────────────────────────────────────────────────

@app.get("/api/health")
def health(s: DbSession = Depends(get_session)):
    return {"status": "ok", "sessions": store.count(s)}


@app.get("/api/presets", response_model=Presets)
def presets():
    return Presets(
        regex_presets=REGEX_PRESETS,
        date_formats=UI_DATE_FORMATS,
        field_types=["string", "integer", "float", "date", "boolean"],
        encodings=[_AUTO, "utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"],
        delimiters={
            _AUTO: None,
            "comma (,)": ",",
            "semicolon (;)": ";",
            "tab (\\t)": "\t",
            "pipe (|)": "|",
        },
        case_modes=["upper", "lower", "title"],
    )


# ──────────────────────────────────────────────────────────────
# FILES
# ──────────────────────────────────────────────────────────────

@app.post("/api/files", response_model=FileResponse)
async def upload_file(
    file: UploadFile = File(...),
    file_type: str = Form("CSV"),
    encoding: str = Form(_AUTO),
    delimiter: str = Form(_AUTO),
    sheet: str = Form(""),
    table_marker: str = Form(""),
    table_index: int = Form(0),
    table_header_mode: str = Form("local"),
    preview_limit: int = Form(500),
    _cap=Depends(require_capability("file.upload")),
    s: DbSession = Depends(get_session),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")

    sheets: list[str] = []
    sheet_used: str | None = None
    table_count = 0
    try:
        if file_type.upper() == "CSV":
            delim = None if delimiter == _AUTO else delimiter
            df, enc_used, delim_used = _files.load_csv_raw(raw, encoding, delim)
        else:
            sheets = _files.list_sheets(raw)
            # Resolve the requested sheet; fall back to the only/first sheet.
            target: object = 0
            if sheet:
                if sheet in sheets:
                    target = sheet
                elif sheet.strip().lstrip("-").isdigit() and int(sheet) < len(sheets):
                    target = int(sheet)
                elif len(sheets) == 1:
                    target = 0
                else:
                    target = 0          # requested sheet absent -> first
            if table_marker.strip():
                # Multi-table sheet: slice to the chosen table.
                chunks = _files.split_tables(raw, target, table_marker)
                table_count = len(chunks)
                df = _files.extract_table(raw, target, table_marker, table_index, table_header_mode)
            else:
                df = _files.load_xlsx_raw(raw, sheet=target)
            sheet_used = sheets[target] if isinstance(target, int) else target
            enc_used, delim_used = "N/A", "N/A"
    except Exception as e:  # noqa: BLE001 — surface load errors to the client
        raise HTTPException(422, f"Could not read the file: {e}")

    sid = store.create(
        s, df,
        file_type=file_type.upper(),
        encoding=str(enc_used),
        delimiter=str(delim_used),
    )
    return FileResponse(
        session_id=sid,
        type=file_type.upper(),
        encoding=str(enc_used),
        delimiter=str(delim_used),
        sheet=sheet_used,
        sheets=sheets,
        table_count=table_count,
        preview=_preview(df, preview_limit),
    )


# ══════════════════════════════════════════════════════════════════════
# Attached sources — extra frames a session can cross-reference in SQL
# ══════════════════════════════════════════════════════════════════════
MAX_SOURCE_ROWS = 200_000    # a source lives in memory next to the session


def _check_declared_schema(s: DbSession, connection: str, scope: str,
                           schema_name: Optional[str], df: pd.DataFrame) -> None:
    """A declared schema is a contract: a column it names but the result
    doesn't have, or a value that doesn't match the declared type, refuses
    the source (422) — never a silent partial accept. No `schema_name`, or
    no schema found under that name, is simply unchecked: declaring one is
    opt-in, never required to use a connection at all."""
    if not schema_name:
        return
    from app.services import schema_check
    row = repo.resolve_variable_rows(s, environment=scope).get(connection)
    if row is None:
        return
    schema = repo.get_variable_schema(s, row.id, schema_name)
    if schema is None:
        return
    try:
        schema_check.validate_against_schema(df, schema.schema_json)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _source_info(sess, name: str, df: pd.DataFrame) -> SourceInfo:
    key = sess.attached_keys.get(name)
    return SourceInfo(name=name, columns=list(df.columns), row_count=int(len(df)),
                      join_local=key[0] if key else None, join_source=key[1] if key else None)


@app.get("/api/files/{sid}/sources", response_model=list[SourceInfo])
def list_sources(sid: str, s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        return [_source_info(sess, n, df) for n, df in sess.attached.items()]


@app.post("/api/files/{sid}/sources/dataset", response_model=SourceInfo)
def attach_dataset_source(sid: str, req: AttachDatasetSource,
                          user=Depends(require_user), s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")
        try:
            d = repo.get_dataset(s, req.dataset_id)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        scope = d.environment or repo.DEFAULT_ENV
        perm = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
        if not repo.can_on_dataset(perm, "read"):
            raise HTTPException(403, f"Aucun accès en lecture à la table « {d.name} ».")

        total = repo.count_rows(s, req.dataset_id)
        if total > MAX_SOURCE_ROWS:
            raise HTTPException(413, f"La table « {d.name} » contient {total} lignes, "
                                     f"au-delà de la limite de {MAX_SOURCE_ROWS} pour une "
                                     f"source attachée.")
        rows = [r.data for r in repo.read_rows(s, req.dataset_id, offset=0, limit=MAX_SOURCE_ROWS)]
        cols = list((d.schema_json or {}).get("columns", []))
        df = pd.DataFrame(rows)
        if cols:
            for c in cols:
                if c not in df.columns:
                    df[c] = ""
            df = df[cols]
        df = df.astype("string").fillna("")
        df, _masked = _ds.mask_encrypted_columns(df)

        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.post("/api/files/{sid}/sources/session", response_model=SourceInfo)
def attach_session_source(sid: str, req: AttachSessionSource,
                          s: DbSession = Depends(get_session)):
    """
    Attach another open tab's working table as a source — the multi-tab
    workspace's way of crossing two tables, reusing the exact same
    `attached` mechanism a dataset or an external source already is. A
    snapshot at attach time, not a live view, same as every other source:
    if the other tab changes afterwards, detach and reattach to refresh.
    """
    try:
        other = store.get(s, req.source_sid)   # read-only: never saved back
    except KeyError:
        raise HTTPException(404, "Session source introuvable ou expirée.")
    df = other.active_df()
    if len(df) > MAX_SOURCE_ROWS:
        raise HTTPException(413, f"Cet onglet contient {len(df)} lignes, au-delà de "
                                 f"la limite de {MAX_SOURCE_ROWS} pour une source attachée.")

    with _session(sid, s) as sess:
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")
        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.post("/api/files/{sid}/sources/upload", response_model=SourceInfo)
async def attach_upload_source(sid: str, name: str = Form(...), file: UploadFile = File(...),
                               s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        name = name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "Fichier vide.")
        try:
            if (file.filename or "").lower().endswith((".xlsx", ".xls")):
                df = _files.load_xlsx_raw(raw, sheet=0)
            else:
                df, _enc, _delim = _files.load_csv_raw(raw, _AUTO, None)
        except Exception as e:  # noqa: BLE001 — surface load errors to the client
            raise HTTPException(422, f"Impossible de lire le fichier : {e}")

        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.post("/api/files/{sid}/sources/external_db", response_model=SourceInfo)
def attach_external_db_source(sid: str, req: AttachExternalDbSource, env: str = "",
        _cap=Depends(require_capability("variables.read")),
        s: DbSession = Depends(get_session)):
    """
    A read-only, parameter-bound query against a saved BDD externe
    connection, attached the same way a table or an uploaded file already
    is — a session source, nothing persisted. Never a second SQL engine:
    `external_db.run_query` is exactly what the `external_db` flow brick
    calls too.
    """
    from app.services import connections, external_db

    with _session(sid, s) as sess:
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")
        query = req.query.strip()
        if not query:
            raise HTTPException(422, "La requête ne peut pas être vide.")

        scope = env or repo.DEFAULT_ENV
        values = repo.resolve_variables(s, environment=scope)
        kinds = repo.resolve_variable_kinds(s, environment=scope)
        try:
            conn = connections.parse_connection(
                req.connection, values.get(req.connection), kinds.get(req.connection), "external_db")
        except ValueError as e:
            raise HTTPException(404, str(e))
        url = conn.get("url")
        if not url:
            raise HTTPException(422, f"La connexion « {req.connection} » n'a pas d'URL.")

        try:
            df = external_db.run_query(url, query, req.params, MAX_SOURCE_ROWS)
        except external_db.QueryTooLarge as e:
            raise HTTPException(413, str(e))
        except Exception as e:  # noqa: BLE001 — surface the query failure to the client
            raise HTTPException(422, f"Échec de la requête : {e}")

        _check_declared_schema(s, req.connection, scope, req.schema_name, df)
        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.post("/api/files/{sid}/sources/api", response_model=SourceInfo)
def attach_api_source(sid: str, req: AttachApiSource, env: str = "",
        _cap=Depends(require_capability("variables.read")),
        s: DbSession = Depends(get_session)):
    """
    Attach the answer of a saved API connection as a session source — either
    data (JSON, walked to `data_path` the same way the `api` flow brick
    does) or a file (CSV/XLSX), chosen explicitly by `response_kind`: an API
    connection point does not itself know which one it answers with, so
    nothing here guesses.
    """
    from app.services import api_source, connections

    with _session(sid, s) as sess:
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")

        scope = env or repo.DEFAULT_ENV
        values = repo.resolve_variables(s, environment=scope)
        kinds = repo.resolve_variable_kinds(s, environment=scope)
        try:
            conn = connections.parse_connection(
                req.connection, values.get(req.connection), kinds.get(req.connection), "api")
        except ValueError as e:
            raise HTTPException(404, str(e))

        base = (conn.get("base_url") or "").rstrip("/")
        rel = req.path.lstrip("/")
        url = f"{base}/{rel}" if rel else base
        if not url:
            raise HTTPException(422, f"La connexion « {req.connection} » n'a pas de base_url.")
        headers = {}
        if conn.get("token"):
            headers[conn.get("auth_header") or "Authorization"] = conn["token"]

        try:
            raw, _status = api_source.call(url, req.method.upper(), headers, req.body, 20.0)
        except api_source.ApiCallError as e:
            raise HTTPException(422, str(e))

        try:
            if req.response_kind == "xlsx":
                df = _files.load_xlsx_raw(raw, sheet=0)
            elif req.response_kind == "csv":
                df, _enc, _delim = _files.load_csv_raw(raw, _AUTO, None)
            else:
                payload = json.loads(raw)
                payload = api_source.walk_json_path(payload, req.data_path)
                if payload is None:
                    payload = []
                if isinstance(payload, dict):
                    payload = [payload]
                if not isinstance(payload, list):
                    raise HTTPException(422, "La réponse JSON n'est ni une liste ni un objet.")
                rows = [r for r in payload if isinstance(r, dict)]
                df = pd.DataFrame(rows).astype("string").fillna("") if rows else pd.DataFrame()
        except api_source.ApiCallError as e:
            raise HTTPException(422, str(e))
        except ValueError:
            raise HTTPException(422, f"{url} n'a pas renvoyé de JSON.")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — surface a bad file/response to the client
            raise HTTPException(422, f"Impossible de lire la réponse : {e}")

        if len(df) > MAX_SOURCE_ROWS:
            raise HTTPException(413, f"La réponse contient plus de {MAX_SOURCE_ROWS} lignes.")

        _check_declared_schema(s, req.connection, scope, req.schema_name, df)
        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.post("/api/files/{sid}/sources/flow", response_model=SourceInfo)
def attach_flow_source(sid: str, req: AttachFlowSource, env: str = "",
        s: DbSession = Depends(get_session)):
    """
    Run another stored flow right now and attach its output — a flow is
    already the same lazily-resolved object a table/BDD externe/API source
    is, so this reuses the exact same resolver a flow-run goes through
    (`source_recipe.build_source_frame`) rather than a second way of
    executing a flow. That flow needs a fixed source of its own
    (source_dataset_id): nothing here can upload a file on its behalf.
    """
    from app.services import source_recipe

    with _session(sid, s) as sess:
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "La source a besoin d'un nom.")
        scope = env or repo.DEFAULT_ENV
        try:
            df = source_recipe.build_source_frame(
                s, {"source_kind": "flow", "name": name, "flow_id": req.flow_id}, scope)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        commit(s)   # the nested flow's Run must be persisted, same as any other run
        if len(df) > MAX_SOURCE_ROWS:
            raise HTTPException(413, f"Le résultat du flux contient plus de {MAX_SOURCE_ROWS} lignes.")
        sess.attached[name] = df
        return _source_info(sess, name, df)


@app.delete("/api/files/{sid}/sources/{name}")
def detach_source(sid: str, name: str, s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        sess.attached.pop(name, None)
        sess.attached_keys.pop(name, None)
        return {"ok": True}


@app.put("/api/files/{sid}/sources/{name}/key", response_model=SourceInfo)
def set_source_key(sid: str, name: str, req: SetSourceKey, s: DbSession = Depends(get_session)):
    """A single-column join key on a source already attached — declared once
    here so a plain computed column can address it as [name.field] instead
    of only through a SQL block. The source's columns aren't known until
    after it's attached, so this is always a separate step from attaching."""
    with _session(sid, s) as sess:
        df = sess.attached.get(name)
        if df is None:
            raise HTTPException(404, f"Source « {name} » introuvable.")
        if req.source_column not in df.columns:
            raise HTTPException(422, f"« {req.source_column} » n'existe pas dans la source « {name} ».")
        sess.attached_keys[name] = (req.local_column, req.source_column)
        return _source_info(sess, name, df)


@app.delete("/api/files/{sid}/sources/{name}/key")
def clear_source_key(sid: str, name: str, s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        sess.attached_keys.pop(name, None)
        return {"ok": True}


@app.post("/api/files/{sid}/sources/{name}/diff")
def diff_source(sid: str, name: str, req: DiffRequest, s: DbSession = Depends(get_session)):
    """Compare this session against an attached source on a chosen key —
    added/removed/changed rows. A report, so a composite key is fine here
    even though the [source.champ] lookup restricts itself to one column."""
    with _session(sid, s) as sess:
        right = sess.attached.get(name)
        if right is None:
            raise HTTPException(404, f"Source « {name} » introuvable.")
        left = sess.active_df().copy()
        right = right.copy()
        # A column already flagged sensitive by the last run must not leak its
        # real value into a diff any more than into the grid itself — same
        # depth-of-masking rule as everywhere else. Both sides are masked
        # (not just the session's), and `diff_frames` excludes them from the
        # comparison entirely rather than letting a masked-vs-real mismatch
        # read as a false "changed" on every row.
        for col in sess.sensitivity:
            if col in left.columns:
                left[col] = _crypto.MASK
            if col in right.columns:
                right[col] = _crypto.MASK
        try:
            return _diff.diff_frames(left, right, req.keys, sensitive=frozenset(sess.sensitivity))
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.post("/api/files/{sid}/header", response_model=TablePreview)
def apply_header(sid: str, req: HeaderRequest, preview_limit: int = 500,
                 s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        sess.work_df = _process.apply_header_config(sess.raw_df, req.header)
        sess.header_cfg = req.header          # remembered so edit-reset can re-apply it
        sess.edits_count = 0                  # header rebuilds work_df from raw -> edits are gone
        return _preview(sess.active_df(), preview_limit)


@app.post("/api/files/{sid}/cells", response_model=EditCellsResponse)
def edit_cells(sid: str, req: EditCellsRequest,
        _cap=Depends(require_capability("file.edit_cells")),
        s: DbSession = Depends(get_session)):
    """
    Apply manual cell edits to the WORKING table (work_df), i.e. the values the
    validation pipeline reads. `column` is the SOURCE column name (pre-rename);
    `index` is the df index returned by /files, /header, /process and /rows.
    Edits do not re-run validation: the previous result becomes stale until the
    next /process. 'AUTO-corriger à la main, revalider ensuite.'
    """
    with _session(sid, s) as sess:
        if len(req.edits) > 10_000:
            raise HTTPException(413, "Too many edits in one call (max 10 000).")
        applied, rejected = 0, []
        for e in req.edits:
            if e.column not in sess.work_df.columns:
                rejected.append({"index": e.index, "column": e.column, "reason": "unknown column"})
                continue
            if e.index not in sess.work_df.index:
                rejected.append({"index": e.index, "column": e.column, "reason": "unknown row"})
                continue
            sess.work_df.at[e.index, e.column] = str(e.value)
            applied += 1
        sess.edits_count += applied
        return EditCellsResponse(
            applied=applied, rejected=rejected,
            edits_total=sess.edits_count, stale=sess.last_df is not None,
        )


@app.post("/api/files/{sid}/cells/reset", response_model=TablePreview)
def reset_cells(sid: str, preview_limit: int = 500, s: DbSession = Depends(get_session)):
    """Discard all manual edits: rebuild work_df from the raw file (re-applying
    the last header treatment if one was set)."""
    with _session(sid, s) as sess:
        sess.work_df = (_process.apply_header_config(sess.raw_df, sess.header_cfg)
                        if sess.header_cfg is not None else sess.raw_df.copy())
        sess.edits_count = 0
        sess.deleted.clear()
        sess.added.clear()
        return _preview(sess.active_df(), preview_limit)


def _columns_from_config(cfg) -> tuple[list[str], dict]:
    """A config's visible columns are its fields' output names, and the rules
    travel with them so the seeded session is ready to validate."""
    cols: list[str] = []
    fields: dict = {}
    for f in cfg.Fields:
        name = f.mapping if (f.rename_output and f.mapping) else (f.name[0] if f.name else None)
        if not name:
            continue
        cols.append(name)
        fields[name] = f
    return cols, fields


def _columns_from_edi_model(model) -> list[str]:
    """The flat-pivot column names of an EDI model — the same names the pivot
    and the generator agree on. `field_names()` groups them by zone (header,
    items, summary); flatten in that order, de-duplicated."""
    by_zone = model.field_names()
    ordered: list[str] = []
    for zone in ("header", "items", "summary"):
        ordered.extend(by_zone.get(zone, []))
    return list(dict.fromkeys(ordered))


@app.post("/api/files/blank", response_model=FileResponse)
def create_blank_session(req: BlankSessionRequest,
        user=Depends(require_capability("file.upload")),
        s: DbSession = Depends(get_session)):
    """
    Start a session from a schema instead of a file: the schema is the source of
    truth, the data comes later (or never). Columns are given explicitly, or
    seeded from a library artefact — a config brings its validation rules along,
    so it can be tried on hand-typed rows without fabricating a CSV.
    """
    seeded_fields: dict | None = None
    if req.artefact_id:
        from app.store_routes import _user_can_view_artefact
        with session_scope() as s2:
            try:
                ver = repo.resolve_ref(s2, req.artefact_id, req.artefact_version)
            except repo.NotFound as e:
                raise HTTPException(404, str(e))
            if not _user_can_view_artefact(s2, user, ver.artefact):
                raise HTTPException(404, f"Artefact {req.artefact_id} introuvable.")
            kind = ver.artefact.kind
            body = ver.body                 # already a deserialized dict, not YAML text
        if kind == "config":
            cfg = _config._from_dict(body)
            columns, seeded_fields = _columns_from_config(cfg)
        elif kind == "edi_model":
            from app.edi_models import EdiModel
            columns = _columns_from_edi_model(EdiModel(**body))
        else:
            raise HTTPException(422, f"A '{kind}' artefact has no columns to seed a session from.")
        if not columns:
            raise HTTPException(422, "That artefact declares no columns.")
    else:
        columns = [c for c in (c.strip() for c in req.columns) if c]
        if not columns:
            raise HTTPException(422, "Provide at least one column, or an artefact to seed from.")
        dupes = [c for c in columns if columns.count(c) > 1]
        if dupes:
            raise HTTPException(422, f"Duplicate column name(s): {', '.join(sorted(set(dupes)))}.")

    df = pd.DataFrame({c: pd.Series(dtype="string") for c in columns})
    sid = store.create(s, df, file_type="MANUAL", encoding="N/A", delimiter="N/A")

    n = max(0, min(int(req.rows), 500))
    if n:
        with store.session(s, sid) as sess:
            for _ in range(n):
                idx = sess.new_index()
                sess.work_df.loc[idx] = {c: "" for c in columns}
                sess.added.add(idx)

    return FileResponse(
        session_id=sid, type="MANUAL", encoding="N/A", delimiter="N/A",
        preview=_preview(store.get(s, sid).active_df(), 500),
        seeded_fields=seeded_fields or None,
    )


@app.post("/api/files/from-external-db", response_model=FileResponse)
def create_session_from_external_db(req: ExternalDbSessionRequest, env: str = "",
        _cap=Depends(require_capability("file.upload")),
        s: DbSession = Depends(get_session)):
    """
    Start a session directly from a read-only, parameter-bound query against
    a saved BDD externe connection — the same doorway a CSV upload is,
    alongside it in Schéma & Règles rather than a second concept. Reuses
    `external_db.run_query`, exactly what an attached source and the
    `external_db` flow brick already call.
    """
    from app.services import connections, external_db

    query = req.query.strip()
    if not query:
        raise HTTPException(422, "La requête ne peut pas être vide.")

    scope = env or repo.DEFAULT_ENV
    values = repo.resolve_variables(s, environment=scope)
    kinds = repo.resolve_variable_kinds(s, environment=scope)
    try:
        conn = connections.parse_connection(
            req.connection, values.get(req.connection), kinds.get(req.connection), "external_db")
    except ValueError as e:
        raise HTTPException(404, str(e))
    url = conn.get("url")
    if not url:
        raise HTTPException(422, f"La connexion « {req.connection} » n'a pas d'URL.")

    try:
        df = external_db.run_query(url, query, req.params, MAX_SOURCE_ROWS)
    except external_db.QueryTooLarge as e:
        raise HTTPException(413, str(e))
    except Exception as e:  # noqa: BLE001 — surface the query failure to the client
        raise HTTPException(422, f"Échec de la requête : {e}")

    _check_declared_schema(s, req.connection, scope, req.schema_name, df)
    sid = store.create(s, df, file_type="SQL", encoding="N/A", delimiter="N/A")
    return FileResponse(session_id=sid, type="SQL", encoding="N/A", delimiter="N/A",
                        preview=_preview(df, 150))


@app.post("/api/files/from-api", response_model=FileResponse)
def create_session_from_api(req: ApiSessionRequest, env: str = "",
        _cap=Depends(require_capability("file.upload")),
        s: DbSession = Depends(get_session)):
    """
    Start a session directly from a saved API connection's answer — data
    (JSON, walked to `data_path`) or a file (CSV/XLSX), chosen explicitly by
    `response_kind` exactly like the attached-source counterpart.
    """
    from app.services import api_source, connections

    scope = env or repo.DEFAULT_ENV
    values = repo.resolve_variables(s, environment=scope)
    kinds = repo.resolve_variable_kinds(s, environment=scope)
    try:
        conn = connections.parse_connection(
            req.connection, values.get(req.connection), kinds.get(req.connection), "api")
    except ValueError as e:
        raise HTTPException(404, str(e))

    base = (conn.get("base_url") or "").rstrip("/")
    rel = req.path.lstrip("/")
    url = f"{base}/{rel}" if rel else base
    if not url:
        raise HTTPException(422, f"La connexion « {req.connection} » n'a pas de base_url.")
    headers = {}
    if conn.get("token"):
        headers[conn.get("auth_header") or "Authorization"] = conn["token"]

    try:
        raw, _status = api_source.call(url, req.method.upper(), headers, req.body, 20.0)
    except api_source.ApiCallError as e:
        raise HTTPException(422, str(e))

    try:
        if req.response_kind == "xlsx":
            df = _files.load_xlsx_raw(raw, sheet=0)
        elif req.response_kind == "csv":
            df, _enc, _delim = _files.load_csv_raw(raw, _AUTO, None)
        else:
            payload = json.loads(raw)
            payload = api_source.walk_json_path(payload, req.data_path)
            if payload is None:
                payload = []
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise HTTPException(422, "La réponse JSON n'est ni une liste ni un objet.")
            rows = [r for r in payload if isinstance(r, dict)]
            df = pd.DataFrame(rows).astype("string").fillna("") if rows else pd.DataFrame()
    except api_source.ApiCallError as e:
        raise HTTPException(422, str(e))
    except ValueError:
        raise HTTPException(422, f"{url} n'a pas renvoyé de JSON.")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — surface a bad file/response to the client
        raise HTTPException(422, f"Impossible de lire la réponse : {e}")

    if len(df) > MAX_SOURCE_ROWS:
        raise HTTPException(413, f"La réponse contient plus de {MAX_SOURCE_ROWS} lignes.")

    _check_declared_schema(s, req.connection, scope, req.schema_name, df)
    sid = store.create(s, df, file_type="API", encoding="N/A", delimiter="N/A")
    return FileResponse(session_id=sid, type="API", encoding="N/A", delimiter="N/A",
                        preview=_preview(df, 150))


def _missing_keys(sensitivity: dict) -> list:
    """Keys a run depends on that are gone or revoked."""
    if not sensitivity:
        return []
    from app.db import session_scope
    from app.db_models import CryptoKey
    from sqlalchemy import select
    wanted = set(sensitivity.values())
    try:
        with session_scope() as s:
            alive = {k.name for k in s.scalars(
                select(CryptoKey).where(CryptoKey.name.in_(wanted),
                                        CryptoKey.active.is_(True)))}
    except Exception:  # noqa: BLE001 — never let this break a run
        return []
    return sorted(wanted - alive)


def _mask_uncovered(uncovered: dict, sensitivity: dict) -> dict:
    """
    The unmapped-values list quotes data verbatim, which on a confidential
    column would hand it over in the clear — the very leak encryption is meant
    to close. Counts stay, values go.
    """
    if not sensitivity:
        return uncovered
    out = {}
    for col, values in (uncovered or {}).items():
        if col in sensitivity:
            out[col] = [{"value": _crypto.MASK, "count": v.get("count", 0),
                        **({"reason": v["reason"]} if "reason" in v else {})}
                        for v in values]
        else:
            out[col] = values
    return out


def _mask_preview(preview, sensitivity: dict):
    """Mask confidential columns in anything shown on screen."""
    if not sensitivity or not preview:
        return preview
    cols = list(preview.columns)
    idx = [i for i, c in enumerate(cols) if c in sensitivity]
    if not idx:
        return preview
    preview.data = [[_crypto.MASK if (i in idx and v) else v for i, v in enumerate(row)]
                    for row in preview.data]
    return preview


@app.get("/api/files/{sid}/preview", response_model=TablePreview)
def preview_active(sid: str, limit: int = 150, s: DbSession = Depends(get_session)):
    """The working table as it stands — edits applied, deleted rows excluded.
    What the client reloads after adding or removing rows."""
    with _session(sid, s) as sess:
        return _preview(sess.active_df(), max(1, min(int(limit), 1000)))


class ToFlowRequest(BaseModel):
    name: str
    environment: str = ""
    save: bool = True
    source: str = "session"          # session | dataset — where the flow reads from
    dataset_name: str = ""


@app.post("/api/files/{sid}/to-flow")
def session_to_flow(sid: str, req: ToFlowRequest, s: DbSession = Depends(get_session),
        _cap=Depends(require_capability("flow.write"))):
    """
    Turn what was just done by hand into a repeatable flow.

    The honest part of this feature is what it *refuses* to carry over. A
    validation run and a computed column are **logic**: they describe a
    treatment and replay on any file. A hand-edited cell and a deleted row are
    **data** — true of this file and no other. Materialising them as flow steps
    would produce a flow that silently corrupts the next file it touches, so
    they are reported as skipped instead, with their count.
    """
    with _session(sid, s) as sess:
        nodes: list[dict] = []
        edges: list[dict] = []
        skipped: list[str] = []

        if req.source == "dataset" and req.dataset_name:
            nodes.append({"id": "src", "type": "dataset",
                          "config": {"name": req.dataset_name}})
        else:
            nodes.append({"id": "src", "type": "session", "config": {"session_id": sid}})

        prev = "src"
        for step in sess.history:
            if step.get("op") == "compute":
                nodes.append({"id": "calc", "type": "compute",
                              "label": "colonnes calculées",
                              "config": {"columns": {c: f"[{c}]" for c in step["columns"]}}})
                edges.append({"from": prev, "to": "calc"})
                prev = "calc"
            elif step.get("op") == "validate":
                rules = {c: {k: v for k, v in r.items()
                             if k in ("type", "regex", "nullable", "length")}
                         for c, r in step.get("rules", {}).items()}
                rules = {c: r for c, r in rules.items() if r}
                if rules:
                    nodes.append({"id": "check", "type": "validate",
                                  "label": "règles de validation",
                                  "config": {"rules": rules}})
                    edges.append({"from": prev, "to": "check"})
                    prev = "check"

        edits = int(getattr(sess, "edits_count", 0) or 0)
        if edits:
            skipped.append(f"{edits} cellule(s) corrigée(s) à la main — donnée, pas logique")
        removed = len(getattr(sess, "deleted", set()) or set())
        if removed:
            skipped.append(f"{removed} ligne(s) supprimée(s) à la main — donnée, pas logique")

        nodes.append({"id": "out", "type": "response", "config": {}})
        edges.append({"from": prev, "to": "out"})

        graph = {"name": req.name or f"flux-{sid[:6]}", "nodes": nodes, "edges": edges}
        try:
            FlowGraph(**graph)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"The recorded steps do not form a valid flow: {e}")

        artefact_id = None
        if req.save:
            try:
                ver = repo.create_artefact(s, "graph", graph["name"], graph,
                                           environment=req.environment or None)
                artefact_id = ver.artefact_id
                commit(s)
            except repo.Conflict as e:
                raise HTTPException(409, str(e))

        return {"graph": graph, "artefact_id": artefact_id, "skipped": skipped,
                "steps": len(nodes) - 1}


class ToTcoRequest(BaseModel):
    source_column: str
    target_column: str
    # a column of the session as TYPE...
    type_column: Optional[str] = None
    # ...or a fixed value for every row, when one table covers only one field
    type_value: str = ""
    artefact_id: Optional[str] = None    # add a version to an existing TCO...
    name: str = ""                       # ...or create a new one
    environment: Optional[str] = None
    description: str = ""


@app.post("/api/files/{sid}/to-tco")
def session_to_tco(sid: str, req: ToTcoRequest,
        user=Depends(require_capability("tco.replace")),
        s: DbSession = Depends(get_session)):
    """
    Turn the active session into a correspondence table — the same doorway
    a fixed CSV upload is, alongside it. Whatever brought the data in
    (a file, a blank session typed by hand, a SQL or API source) already
    left it as an ordinary working table; this just picks which columns
    play SOURCE_VALUE and TARGET_LABEL rather than requiring a second,
    TCO-specific loading path.

    No validation run is required: a TCO is reference data, not itself
    checked against field rules, so `active_df()` (edits applied, deleted
    rows excluded) is enough — unlike a dataset write, which needs a run.
    """
    with _session(sid, s) as sess:
        df = sess.active_df()
        for col in (req.source_column, req.target_column):
            if col not in df.columns:
                raise HTTPException(422, f"Colonne « {col} » absente de la session.")
        if df.empty:
            raise HTTPException(422, "La session ne contient aucune ligne.")
        out = pd.DataFrame({"SOURCE_VALUE": df[req.source_column].astype(str),
                            "TARGET_LABEL": df[req.target_column].astype(str)})
        if req.type_column:
            if req.type_column not in df.columns:
                raise HTTPException(422, f"Colonne « {req.type_column} » absente de la session.")
            out.insert(0, "TYPE", df[req.type_column].astype(str))
        elif req.type_value.strip():
            out.insert(0, "TYPE", req.type_value.strip())
        csv_text = out.to_csv(index=False)

    from app.services import store_service
    from app.store_routes import _check_kind_capability
    try:
        body = store_service.normalise_body("tco", body=None, yaml=None,
                                            computed=None, csv=csv_text)
    except store_service.BadBody as e:
        raise HTTPException(422, str(e))
    try:
        if req.artefact_id:
            try:
                art = repo.get_artefact(s, req.artefact_id)
            except repo.NotFound as e:
                raise HTTPException(404, str(e))
            if art.kind != "tco":
                raise HTTPException(409, f"« {art.name} » est un {art.kind}, pas un tco.")
            # The capability gate above only checked the query-string
            # environment — appending a version writes into whatever
            # environment *owns* this artefact, which may be a different
            # one, so that is what the caller's role is actually checked
            # against (same reasoning as store_routes.py's add_version).
            _check_kind_capability(s, user, "tco", art.environment)
            ver = repo.add_version(s, req.artefact_id, body, note="depuis une session")
            aid = req.artefact_id
        else:
            if not req.name.strip():
                raise HTTPException(422, "Donnez un nom au nouveau TCO.")
            ver = repo.create_artefact(s, "tco", req.name.strip(), body,
                                       req.description, environment=req.environment)
            aid = ver.artefact_id
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return {"artefact_id": aid, "version_no": ver.version_no, "rows": int(len(out))}


@app.post("/api/files/{sid}/rows/add", response_model=RowsMutationResponse)
def add_rows(sid: str, req: AddRowsRequest,
        _cap=Depends(require_capability("file.edit_cells")),
        s: DbSession = Depends(get_session)):
    """
    Append blank rows, or duplicate an existing one. New indices come from a
    monotonic counter and are never reused: the row index is the stable key the
    edit overlay, the report and the dataset writer all rely on.
    """
    with _session(sid, s) as sess:
        n = max(1, min(int(req.count), 500))
        if req.copy_from is not None and req.copy_from not in sess.work_df.index:
            raise HTTPException(404, f"Row {req.copy_from} does not exist.")
        template = (sess.work_df.loc[req.copy_from].to_dict() if req.copy_from is not None
                    else {c: "" for c in sess.work_df.columns})
        new_idx = [sess.new_index() for _ in range(n)]
        block = pd.DataFrame([template] * n, index=new_idx).astype(str)
        sess.work_df = pd.concat([sess.work_df, block])
        sess.added.update(new_idx)
        return _rows_state(sess, added=n, new_indices=new_idx)


@app.post("/api/files/{sid}/rows/delete", response_model=RowsMutationResponse)
def delete_rows(sid: str, req: DeleteRowsRequest,
        _cap=Depends(require_capability("file.edit_cells")),
        s: DbSession = Depends(get_session)):
    """
    Logical deletion: rows leave the active table but stay in `work_df`, so
    /cells/reset brings them back and the count is reportable. `all_filtered`
    deletes everything matching the current filters and/or statuses of the last
    run — the whole point of 'filter the errors, drop the batch'.
    """
    with _session(sid, s) as sess:
        targets: set[int] = {i for i in req.indices if i in sess.work_df.index}

        if req.all_filtered:
            if sess.last_df is None:
                raise HTTPException(409, "Run validation first to delete a filtered batch.")
            cols = [c for c in (sess.last_cols or []) if c in sess.last_df.columns]
            df = sess.last_df[cols] if cols else sess.last_df.head(0)
            if req.filters:
                df = _apply_filters(df, req.filters)
            if req.statuses:
                wanted = {st.upper() for st in req.statuses}
                validation = sess.last_validation or {}
                clean_mask = sess.last_clean_mask or {}
                computed = set(sess.last_computed or [])
                keep = []
                for idx in df.index:
                    for col in cols:
                        if col in computed:
                            continue
                        v_series = validation.get(col)
                        res = v_series.get(idx, "OK") if v_series is not None else "OK"
                        clean = bool(clean_mask.get(col, pd.Series(dtype=bool)).get(idx, False))
                        if _status_of(str(res), clean) in wanted:
                            keep.append(idx)
                            break
                df = df.loc[keep]
            targets |= {int(i) for i in df.index if i in sess.work_df.index}

        before = len(targets - sess.deleted)
        sess.deleted |= targets
        return _rows_state(sess, deleted=before)


@app.post("/api/files/{sid}/rows/restore", response_model=RowsMutationResponse)
def restore_rows(sid: str, req: DeleteRowsRequest, s: DbSession = Depends(get_session)):
    """Undo a logical deletion — all of it, or the given indices."""
    with _session(sid, s) as sess:
        if req.indices:
            back = {i for i in req.indices if i in sess.deleted}
        else:
            back = set(sess.deleted)
        sess.deleted -= back
        return _rows_state(sess, restored=len(back))


@app.post("/api/files/{sid}/rows/reorder", response_model=TablePreview)
def reorder_row(sid: str, req: ReorderRowRequest, preview_limit: int = 500,
                s: DbSession = Depends(get_session)):
    """
    Move one row to sit right after another (or to the very start). A real
    permutation of `work_df`'s row order — not a display trick — so the new
    order survives export and writing to a table, exactly like a hand-typed
    or reloaded file would read back in whatever order its rows were in.
    """
    with _session(sid, s) as sess:
        order = list(sess.work_df.index)
        if req.index not in order:
            raise HTTPException(404, f"Row {req.index} does not exist.")
        order.remove(req.index)
        if req.after is None:
            order.insert(0, req.index)
        else:
            if req.after not in order:
                raise HTTPException(404, f"Row {req.after} does not exist.")
            order.insert(order.index(req.after) + 1, req.index)
        sess.work_df = sess.work_df.loc[order]
        return _preview(sess.active_df(), preview_limit)


def _rows_state(sess, *, added: int = 0, deleted: int = 0, restored: int = 0,
                new_indices: list | None = None) -> RowsMutationResponse:
    return RowsMutationResponse(
        added=added, deleted=deleted, restored=restored,
        total_rows=int(len(sess.active_df())),
        deleted_total=len(sess.deleted), added_total=len(sess.added),
        stale=sess.last_df is not None,
        new_indices=list(new_indices or []),
    )


@app.post("/api/files/{sid}/tco", response_model=TcoResponse)
async def upload_tco(
    sid: str,
    file: UploadFile = File(...),
    delimiter: str = Form("AUTO"),
    encoding: str = Form("AUTO"),
    s: DbSession = Depends(get_session),
):
    with _session(sid, s) as sess:
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "Empty TCO file.")
        try:
            delim = None if delimiter in ("AUTO", "") else delimiter
            tco_df = _tco.load_tco(raw, delimiter=delim, encoding=encoding)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e))
        sess.tco_df = tco_df
        sess.tco_artefact_id = None       # a raw file, not backed by any artefact
        return TcoResponse(rows=int(len(tco_df)), labels=_tco.get_available_labels(tco_df))


class TcoFromArtefactRequest(BaseModel):
    artefact_id: str
    version_no: Optional[int] = None


@app.post("/api/files/{sid}/tco/from-artefact", response_model=TcoResponse)
def tco_from_artefact(sid: str, req: TcoFromArtefactRequest,
        user=Depends(require_user), s: DbSession = Depends(get_session)):
    """
    Attach a TCO artefact from the library, by reference — not a copy of a
    file the operator happens to have lying around, but the shared,
    admin-maintained table an environment was granted read access to.
    Defaults to the artefact's latest version, so an admin's edit reaches
    every session started afterward without anyone re-uploading anything.
    """
    from app.store_routes import _user_can_view_artefact
    try:
        art = repo.get_artefact(s, req.artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if art.kind != "tco":
        raise HTTPException(409, f"« {art.name} » est un {art.kind}, pas un tco.")
    if not _user_can_view_artefact(s, user, art):
        raise HTTPException(404, f"Artefact {req.artefact_id} introuvable.")
    try:
        ver = repo.resolve_ref(s, req.artefact_id, req.version_no)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    csv_text = (ver.body or {}).get("csv", "")
    if not csv_text.strip():
        raise HTTPException(422, "Cette table de correspondance est vide.")

    with _session(sid, s) as sess:
        try:
            tco_df = _tco.load_tco(csv_text.encode("utf-8"), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e))
        sess.tco_df = tco_df
        sess.tco_artefact_id = req.artefact_id
        return TcoResponse(rows=int(len(tco_df)), labels=_tco.get_available_labels(tco_df),
                           artefact_id=req.artefact_id)


@app.post("/api/files/{sid}/process", response_model=ProcessResponse)
def process(sid: str, req: ProcessRequest, env: str = "",
        _cap=Depends(require_capability("file.process")),
        s: DbSession = Depends(get_session)):
    with _session(sid, s) as sess:
        fields: dict[str, FieldConfig] = req.fields
        # One or more identifier fields (every field marked as identifier).
        id_fields = [c for c in req.visible_cols
                     if fields.get(c) and getattr(fields[c], "identifiant", False)]
        # Known *before* the SQL step runs: a cross-source query must never see a
        # declared-confidential column's real value, only its mask.
        declared_sensitive = frozenset(c for c, f in fields.items() if getattr(f, "sensitive", None))

        # A référentiel variable is referenced by name, resolved fresh here — never
        # trusted from the client, and never a secret even if the requested name
        # is one (the picker already excludes secrets; this is the "fail closed"
        # backstop for a request built by hand).
        ref_values: dict[str, str] = {}
        if req.ref_variables:
            resolved = repo.resolve_variables(s, environment=env or repo.DEFAULT_ENV)
            secrets = repo.secret_names(s)
            ref_values = {n: resolved[n] for n in req.ref_variables
                         if n in resolved and n not in secrets}
        effective_variables = {**ref_values, **req.variables}   # a hand-typed variable wins on collision

        try:
            result = _process.run_pipeline(
                df_edited=sess.active_df(),
                visible_cols=req.visible_cols,
                field_configs=fields,
                tco_df=sess.tco_df,
                identifier_fields=id_fields,
                computed=[(c.name, c.expression) for c in req.computed],
                sql_computed=[(c.name, c.expression, c.mode) for c in req.sql_computed],
                style_rules=[(r.column, r.expression) for r in req.style_rules],
                attached=sess.attached,
                attached_keys=sess.attached_keys,
                sensitive_cols=declared_sensitive,
                report_flagged_only=True,
                variables=effective_variables,
            )

            df_post    = result["df"]
            # Safety net: never let duplicate column names through (they would make
            # df[col] return a DataFrame and crash the per-cell loop).
            if df_post.columns.duplicated().any():
                df_post = df_post.loc[:, ~df_post.columns.duplicated()]
            validation = result["validation"]
            clean_mask = result["clean_mask"]
            computed_names = result["computed_names"]

            cols = _display_columns(req.visible_cols, fields, df_post)
            cols += [c for c in computed_names if c not in cols]      # append computed cols
            sess.last_df = df_post                                    # keep for export
            sess.last_cols = cols
            sess.last_validation = validation                         # for paginated row fetches
            sess.last_clean_mask = clean_mask
            sess.last_computed = computed_names
            sess.last_styles = result.get("styles", {})               # for paginated row fetches
            sess.identifier_fields = id_fields

            # ── confidentiality ──────────────────────────────────────────
            # Declared marks, then propagation: a column computed from a
            # confidential one is itself confidential. Doing this here rather than
            # at display time means every consumer downstream — preview, report,
            # export, dataset write — reads the same map.
            declared = {c: f.sensitive for c, f in fields.items()
                        if getattr(f, "sensitive", None)}
            derived = {c.name: c.expression for c in (req.computed or [])}
            sensitivity = _crypto.propagate(declared, derived)
            sess.sensitivity = sensitivity

            # Mask the report DataFrame itself, once, before it is cached —
            # `sess.last_report` (read by the /report download route) and the
            # `report` embedded in this very response both derive from it, so
            # this is the one place that protects both. Found live: the report
            # also carries the raw value inside `resultat`'s free-text message
            # (e.g. `check_type KO — "abc"`), not only in its own dedicated
            # columns — masking only valeur_originale/valeur_finale would have
            # left it leaking through the message, the exact "value comes back
            # through a side channel" failure mode this project has hit before.
            report_df = result["report"]
            if sensitivity and len(report_df):
                sens_rows = report_df["colonne"].isin(sensitivity)
                if sens_rows.any():
                    report_df = report_df.copy()
                    for _col in ("valeur_originale", "valeur_finale", "resultat"):
                        report_df.loc[sens_rows, _col] = _crypto.MASK
                    result["report"] = report_df
            sess.last_report = report_df                              # full report for /report
            # A key that no longer exists makes the configuration unusable: better a
            # clear refusal than a run quietly producing masked nonsense.
            missing = _missing_keys(sensitivity)
            sess.missing_keys = missing
            # The config already declared a type per column; carry it so a dataset
            # records a real schema instead of defaulting every column to string.
            sess.last_field_types = {c: getattr(fields[c], "type", "string")
                                     for c in cols if c in fields}
            # Replayable logic: the rules that were applied, and the derived columns.
            sess.history = [h for h in sess.history if h.get("op") != "validate"]
            sess.history.append({"op": "validate", "columns": list(cols),
                                 "rules": {c: fields[c].model_dump(exclude_none=True)
                                           for c in cols if c in fields}})
            sql_names = [n for n in computed_names
                        if n in {c.name for c in req.sql_computed}]
            plain_names = [n for n in computed_names if n not in sql_names]
            if plain_names:
                sess.history = [h for h in sess.history if h.get("op") != "compute"]
                sess.history.append({"op": "compute", "columns": list(plain_names)})
            if sql_names:
                sess.history = [h for h in sess.history if h.get("op") != "sql_compute"]
                sess.history.append({"op": "sql_compute", "columns": list(sql_names)})
            head = df_post[cols].head(req.preview_limit) if cols else df_post.head(0)

            styles_by_col = result.get("styles") or {}
            data, status, styles, row_index = [], [], [], []
            for idx, row in zip(head.index, head.itertuples(index=False, name=None)):
                row_index.append(int(idx))
                data.append([_cell(v) for v in row])
                row_status = []
                row_styles = []
                for col in cols:
                    if col in computed_names:
                        row_status.append("COMPUTED")
                        continue
                    v_series = validation.get(col)
                    res = v_series.get(idx, "OK") if v_series is not None else "OK"
                    clean = bool(clean_mask.get(col, pd.Series(dtype=bool)).get(idx, False))
                    row_status.append(_status_of(str(res), clean))
                for col in cols:
                    tok = styles_by_col.get(col)
                    row_styles.append(str(tok.get(idx, "")) if tok is not None else "")
                status.append(row_status)
                styles.append(row_styles)

            report = result["report"]
            if len(report) > _REPORT_CAP:
                report = report.head(_REPORT_CAP)
            report_rows = [
                {**{k: _cell(v) if k != "id" else v for k, v in rec.items()}}
                for rec in report.to_dict(orient="records")
            ]
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — surface a clean error instead of a raw 500
            raise HTTPException(422, f"Processing failed: {e}")

        # Mask the grid preview before the response is built — the report is
        # already masked above, on the DataFrame itself, before `report_rows`
        # was built from it.
        if sensitivity:
            _sens_idx = [i for i, c in enumerate(cols) if c in sensitivity]
            if _sens_idx:
                data = [[_crypto.MASK if (i in _sens_idx and v) else v
                         for i, v in enumerate(row)] for row in data]

        return ProcessResponse(
            columns=cols,
            data=data,
            status=status,
            styles=styles,
            style_errors=result.get("style_errors", {}),
            index=row_index,
            computed=computed_names,
            compute_errors=result["compute_errors"],
            stats=result["stats"],
            report=report_rows,
            tco_uncovered=_mask_uncovered(result.get("tco_uncovered", {}), sensitivity),
            sensitivity=sensitivity, missing_keys=missing,
            warnings=result.get("warnings", []),
        )


@app.post("/api/expression/check", response_model=ExpressionResult)
def check_expression(req: ExpressionCheck):
    from app.services.compute_service import ComputeService
    err = ComputeService().validate_expression(req.expression)
    return ExpressionResult(ok=err is None, error=err)


@app.get("/api/files/{sid}/rows", response_model=RowsResponse)
def get_rows(
    sid: str,
    offset: int = 0,
    limit: int = 100,
    filters: str = "",
    sort_col: str = "",
    sort_dir: str = "asc",
    s: DbSession = Depends(get_session),
):
    """
    A page of the last processed table, with per-cell status. Filtering and
    sorting apply to the WHOLE file (not the on-screen sample), so the returned
    `total` is the true filtered count out of `total_all`.
    """
    with _session(sid, s) as sess:
        if sess.last_df is None:
            raise HTTPException(409, "Run validation first to browse rows.")

        cols = [c for c in (sess.last_cols or []) if c in sess.last_df.columns]
        df = sess.last_df[cols] if cols else sess.last_df.head(0)
        total_all = int(len(df))

        if filters:
            try:
                fmap = json.loads(filters)
            except json.JSONDecodeError:
                fmap = {}
            df = _apply_filters(df, fmap or {})
        total = int(len(df))

        if sort_col and sort_col in df.columns:
            asc = sort_dir != "desc"
            col_ser = df[sort_col]
            num = pd.to_numeric(col_ser, errors="coerce")
            if num.notna().any():                       # numeric-ish column
                order = num.sort_values(ascending=asc, kind="stable", na_position="last").index
            else:
                order = col_ser.astype(str).sort_values(ascending=asc, kind="stable").index
            df = df.loc[order]

        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        page = df.iloc[offset:offset + limit]

        validation = sess.last_validation or {}
        clean_mask = sess.last_clean_mask or {}
        computed = set(sess.last_computed or [])
        styles_by_col = sess.last_styles or {}    # cached at /process — never recomputed here

        data, status, styles, row_index = [], [], [], []
        for idx, row in zip(page.index, page.itertuples(index=False, name=None)):
            row_index.append(int(idx))
            data.append([_cell(v) for v in row])
            row_status = []
            row_styles = []
            for col in cols:
                if col in computed:
                    row_status.append("COMPUTED")
                    continue
                v_series = validation.get(col)
                res = v_series.get(idx, "OK") if v_series is not None else "OK"
                clean = bool(clean_mask.get(col, pd.Series(dtype=bool)).get(idx, False))
                row_status.append(_status_of(str(res), clean))
            for col in cols:
                tok = styles_by_col.get(col)
                row_styles.append(str(tok.get(idx, "")) if tok is not None else "")
            status.append(row_status)
            styles.append(row_styles)

        # Same masking as /process — this route is the grid's actual data
        # source once a table is validated (`serverMode` in DataTable.tsx is
        # simply `!!result`), so a mask applied only in /process's own
        # response body never reaches the screen at all. Found live: the
        # confidentiality UI's own end-to-end QA was the first time any
        # column was ever marked sensitive and then actually viewed through
        # a browser, past this route rather than /process's response.
        if sess.sensitivity:
            _sens_idx = [i for i, c in enumerate(cols) if c in sess.sensitivity]
            if _sens_idx:
                data = [[_crypto.MASK if (i in _sens_idx and v) else v
                         for i, v in enumerate(row)] for row in data]

        return RowsResponse(
            columns=cols, data=data, status=status, styles=styles,
            total=total, total_all=total_all, offset=offset, limit=limit,
            index=row_index,
        )


@app.get("/api/files/{sid}/report")
def get_report(
    sid: str,
    shape: str = "long",                 # long | by_id | pivot
    statuses: str = "ERROR,MAPPING_KO,NO_TCO",
    columns: str = "",                   # restrict to these report columns (comma list)
    value: str = "status",               # pivot cell: status | message | final
    fmt: str = "json",                   # json | csv | xlsx
    download: int = 0,
    _cap=Depends(require_capability("report.read")),
    s: DbSession = Depends(get_session),
):
    """
    Flexible report generation over the last run. The same data can be shaped as:
      • long   — one row per flagged cell (id, column, original, final, status, message)
      • by_id  — grouped per id: [{id, errors:[{column, value, status, message}]}]
      • pivot  — one row per id, one column per field (cell = status / message / final)
    Filter by `statuses` and `columns`. Output as json (preview), csv or xlsx.
    """
    with _session(sid, s) as sess:
        rep = sess.last_report
        if rep is None:
            raise HTTPException(409, "Run validation first to build a report.")

        df = rep.copy()
        want = [st.strip().upper() for st in statuses.split(",") if st.strip()]
        if want:
            df = df[df["statut"].isin(want)]
        if columns.strip():
            keep = [c.strip() for c in columns.split(",") if c.strip()]
            df = df[df["colonne"].isin(keep)]

        multi_id = bool(sess.identifier_fields and len(sess.identifier_fields) > 1)
        id_label = " | ".join(sess.identifier_fields) if sess.identifier_fields else "row"

        # ── shape ─────────────────────────────────────────────────────
        if shape == "by_id":
            groups: dict[object, list[dict]] = {}
            order: list[object] = []
            for _, r in df.iterrows():
                k = r["id"]
                if k not in groups:
                    groups[k] = []
                    order.append(k)
                msg = str(r["resultat"])
                groups[k].append({"column": str(r["colonne"]), "value": str(r["valeur_finale"]),
                                  "status": str(r["statut"]), "message": "" if msg == "OK" else msg})
            payload = [{"id": k, "errors": groups[k]} for k in order]
            if fmt == "json" and not download:
                return JSONResponse(payload)
            if fmt == "csv":
                # flatten for CSV
                flat = pd.DataFrame([
                    {"id": g["id"], "column": e["column"], "value": e["value"],
                     "status": e["status"], "message": e["message"]}
                    for g in payload for e in g["errors"]
                ])
                return _report_file(flat, "report_by_id", "csv")
            if fmt == "xlsx":
                flat = pd.DataFrame([
                    {"id": g["id"], "column": e["column"], "value": e["value"],
                     "status": e["status"], "message": e["message"]}
                    for g in payload for e in g["errors"]
                ])
                return _report_file(flat, "report_by_id", "xlsx")
            return JSONResponse(payload)

        if shape == "pivot":
            valcol = {"status": "statut", "message": "resultat", "final": "valeur_finale"}.get(value, "statut")
            if len(df):
                piv = df.pivot_table(index="id", columns="colonne", values=valcol,
                                     aggfunc=lambda s: " ; ".join(map(str, s)))
                piv = piv.reset_index().rename(columns={"id": id_label})
            else:
                piv = pd.DataFrame(columns=[id_label])
            if fmt == "csv":
                return _report_file(piv, "report_pivot", "csv")
            if fmt == "xlsx":
                return _report_file(piv, "report_pivot", "xlsx")
            return JSONResponse(json.loads(piv.to_json(orient="records")))

        # ── long (default) ────────────────────────────────────────────
        out = df.rename(columns={"id": id_label, "colonne": "column",
                                 "valeur_originale": "original", "valeur_finale": "final",
                                 "resultat": "message", "statut": "status"})
        out = out[[id_label, "column", "original", "final", "status", "message"]]
        if fmt == "csv":
            return _report_file(out, "report", "csv")
        if fmt == "xlsx":
            return _report_file(out, "report", "xlsx")
        return JSONResponse(json.loads(out.to_json(orient="records")))


def _report_file(df: "pd.DataFrame", base_name: str, fmt: str):
    """Return a DataFrame as a downloadable CSV or XLSX response."""
    if fmt == "xlsx":
        buf = io.BytesIO(); df.to_excel(buf, index=False, na_rep=""); buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.xlsx"'})
    content = df.to_csv(index=False, sep=";", na_rep="")
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'})


@app.get("/api/files/{sid}/export")
def export_table(
    sid: str,
    fmt: str = "csv",
    encoding: str = "utf-8",
    delimiter: str = ";",
    filename: str = "export",
    filters: str = "",
    style: bool = False,
    _cap=Depends(require_capability("file.export")),
    s: DbSession = Depends(get_session),
):
    """
    Export the processed table (or the working table if nothing has been run
    yet) as CSV or XLSX, with a chosen encoding and delimiter.

    `filters` is an optional JSON object {column: text}; when given, only rows
    where every listed column contains its text (case-insensitive) are exported.
    Filtering applies to the WHOLE file, not just the on-screen sample.
    """
    with _session(sid, s) as sess:
        df = sess.last_df if sess.last_df is not None else sess.active_df()
        cols = sess.last_cols if sess.last_cols else list(df.columns)
        cols = [c for c in cols if c in df.columns]
        out_df = df[cols] if cols else df

        if filters:
            try:
                fmap = json.loads(filters)
            except json.JSONDecodeError:
                fmap = {}
            out_df = _apply_filters(out_df, fmap or {})

        # A confidential column masked on screen must not still leave in the
        # clear the moment it's written to a file — the export is exactly
        # the kind of "next exit" crypto_service.py's own docstring warns
        # never to forget. Found live: never wired to sensitivity at all
        # before this.
        if sess.sensitivity:
            out_df = out_df.copy()
            for col in out_df.columns:
                if col in sess.sensitivity:
                    out_df[col] = out_df[col].map(_crypto.mask_value)

        safe = "".join(ch for ch in filename if ch.isalnum() or ch in (" ", "-", "_")).strip() or "export"

        if fmt.lower() == "pivot":
            # No mapping to build by hand for a plain export: every visible column
            # becomes an item-scope link and nothing groups into a head, so
            # flat_to_pivot folds the whole table into one head-less document —
            # exactly the generic {head, items} shape, reusing the real engine.
            from app.mapping_models import Mapping, MappingLink
            from app.services import pivot_service
            identity = Mapping(name="export", links=[
                MappingLink(pivot=str(c), source=str(c), scope="item") for c in out_df.columns])
            records = pivot_service.flat_to_pivot(out_df, identity, group_by="")
            content = json.dumps(records, ensure_ascii=False, indent=2)
            return StreamingResponse(
                io.BytesIO(content.encode("utf-8")), media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{safe}.json"'},
            )

        if fmt.lower() == "xlsx":
            buf = io.BytesIO()
            # Style rules already compute a per-cell token for the on-screen
            # grid (`sess.last_styles`, set at /process) — applying it here
            # too is the only new work; nothing is recomputed.
            if style and sess.last_styles:
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    out_df.to_excel(writer, index=False, na_rep="", sheet_name="export")
                    xlsx_style.apply_xlsx_styles(writer.sheets["export"], out_df, sess.last_styles)
            else:
                out_df.to_excel(buf, index=False, na_rep="")
            buf.seek(0)
            return StreamingResponse(
                buf,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'},
            )

        content = out_df.to_csv(index=False, sep=delimiter, na_rep="")
        try:
            data = content.encode(encoding, errors="replace")
        except LookupError:
            data = content.encode("utf-8", errors="replace")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'},
        )


@app.post("/api/pipeline", response_model=PipelineResponse)
async def run_full_pipeline(
    file: UploadFile = File(...),
    config: str = Form(...),                 # YAML config text
    tco: UploadFile | None = File(None),     # optional TCO file
    computed: str = Form(""),                # optional JSON [{name, expression}]
    export_filename: str = Form("export"),
):
    """
    One-shot automation endpoint. Delegates to PipelineEngine (shared with
    /api/flows/{id}/run) and returns the staged result:
      config -> load -> structure -> strict_header/min_header -> tco -> validation -> done.
    """
    # -- parse config (route-level stage) --------------------------
    try:
        fc = _config.from_yaml(config)
    except Exception as e:  # noqa: BLE001
        return PipelineResponse(ok=False, stage="config", error=f"Config illisible : {e}")

    raw = await file.read()
    if not raw:
        return PipelineResponse(ok=False, stage="load", error="Fichier vide.")

    tco_bytes = None
    if tco is not None:
        tco_bytes = await tco.read() or None

    comp_list: list[tuple[str, str]] = []
    if computed.strip():
        try:
            arr = json.loads(computed)
            if isinstance(arr, dict):
                arr = arr.get("computed", [])
            comp_list = [(c["name"], c["expression"]) for c in arr
                         if c.get("name") and c.get("expression")]
        except Exception:  # noqa: BLE001
            comp_list = []

    res = _engine.run(raw=raw, fc=fc, tco_bytes=tco_bytes, computed=comp_list,
                      export_filename=export_filename, apply_filters=_apply_filters)
    from app.store_routes import _engine_to_response
    return _engine_to_response(res)


@app.delete("/api/files/{sid}")
def drop(sid: str, s: DbSession = Depends(get_session)):
    store.drop(s, sid)
    return {"dropped": sid}


# ──────────────────────────────────────────────────────────────
# CONFIG — YAML round-trip
# ──────────────────────────────────────────────────────────────

@app.post("/api/config/export", response_model=ExportResponse)
def export_yaml(req: ExportRequest):
    try:
        file_config = _config.build_file_config(
            file_type=req.type,
            encoding=req.encoding,
            delimiter=req.delimiter,
            header_config=req.header,
            field_configs=req.fields,
            visible_cols=req.visible_cols,
            sheet=req.sheet,
            filters=req.filters,
            strict_header=req.strict_header,
            min_header=req.min_header,
            variables=req.variables,
            ref_variables=req.ref_variables,
            table_marker=req.table_marker,
            table_index=req.table_index,
            table_header_mode=req.table_header_mode,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return ExportResponse(yaml=_config.to_yaml(file_config))


@app.post("/api/config/import", response_model=ImportResponse)
def import_yaml(req: ImportRequest):
    try:
        file_config = _config.from_yaml(req.yaml)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Invalid YAML: {e}")

    match: Optional[MatchInfo] = None
    if req.columns:
        m = _config.match_fields_to_columns(file_config, req.columns)
        match = MatchInfo(matched=m["matched"], unmatched=m["unmatched"], unused=m["unused"])
    return ImportResponse(file_config=file_config, match=match)


# ──────────────────────────────────────────────────────────────
@contextmanager
def _session(sid: str, s: DbSession):
    """`with _session(sid, s) as sess: sess.field = ...` — mutating `sess`
    inside the block persists it on a clean exit; an exception discards the
    mutation instead of saving a half-edited session (session.py docstring)."""
    try:
        sess = store.get(s, sid)
    except KeyError:
        raise HTTPException(404, "Session not found or expired. Re-upload the file.")
    yield sess
    store.save(s, sid, sess)
