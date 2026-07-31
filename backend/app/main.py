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

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.models import (
    AddRowsRequest, BlankSessionRequest, CellEdit, DeleteRowsRequest, EditCellsRequest, EditCellsResponse,
    RowsMutationResponse,
    ExportRequest, ExportResponse, ExpressionCheck, ExpressionResult,
    FieldConfig, FileResponse, HeaderRequest, ImportRequest, ImportResponse,
    MatchInfo, Presets, ProcessRequest, ProcessResponse, ProcessStats, RowsResponse, TablePreview, TcoResponse,
    PipelineResponse, SourceInfo, AttachDatasetSource, ReorderRowRequest,
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
from app.session import store
from app.db import commit, get_session, init_db, session_scope
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

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    init_db()                          # bring the artefact store schema to head
    yield

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    """
    out, seen = [], set()
    for col in visible_cols:
        fc = fields.get(col)
        wants_rename = bool(fc and fc.mapping and getattr(fc, "rename_output", True) and fc.mapping != col)
        if col in df_post.columns:
            final = col                                   # rename skipped or none
        elif wants_rename and fc.mapping in df_post.columns:
            final = fc.mapping                            # rename was applied
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
def health():
    return {"status": "ok", "sessions": store.count()}


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
        df,
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


@app.get("/api/files/{sid}/sources", response_model=list[SourceInfo])
def list_sources(sid: str):
    sess = _session(sid)
    return [SourceInfo(name=n, columns=list(df.columns), row_count=int(len(df)))
            for n, df in sess.attached.items()]


@app.post("/api/files/{sid}/sources/dataset", response_model=SourceInfo)
def attach_dataset_source(sid: str, req: AttachDatasetSource,
                          user=Depends(require_user), s: DbSession = Depends(get_session)):
    sess = _session(sid)
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
    return SourceInfo(name=name, columns=list(df.columns), row_count=int(len(df)))


@app.post("/api/files/{sid}/sources/upload", response_model=SourceInfo)
async def attach_upload_source(sid: str, name: str = Form(...), file: UploadFile = File(...)):
    sess = _session(sid)
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
    return SourceInfo(name=name, columns=list(df.columns), row_count=int(len(df)))


@app.delete("/api/files/{sid}/sources/{name}")
def detach_source(sid: str, name: str):
    sess = _session(sid)
    sess.attached.pop(name, None)
    return {"ok": True}


@app.post("/api/files/{sid}/header", response_model=TablePreview)
def apply_header(sid: str, req: HeaderRequest, preview_limit: int = 500):
    sess = _session(sid)
    sess.work_df = _process.apply_header_config(sess.raw_df, req.header)
    sess.header_cfg = req.header          # remembered so edit-reset can re-apply it
    sess.edits_count = 0                  # header rebuilds work_df from raw -> edits are gone
    return _preview(sess.active_df(), preview_limit)


@app.post("/api/files/{sid}/cells", response_model=EditCellsResponse)
def edit_cells(sid: str, req: EditCellsRequest,
        _cap=Depends(require_capability("file.edit_cells"))):
    """
    Apply manual cell edits to the WORKING table (work_df), i.e. the values the
    validation pipeline reads. `column` is the SOURCE column name (pre-rename);
    `index` is the df index returned by /files, /header, /process and /rows.
    Edits do not re-run validation: the previous result becomes stale until the
    next /process. 'AUTO-corriger à la main, revalider ensuite.'
    """
    sess = _session(sid)
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
def reset_cells(sid: str, preview_limit: int = 500):
    """Discard all manual edits: rebuild work_df from the raw file (re-applying
    the last header treatment if one was set)."""
    sess = _session(sid)
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
def create_blank_session(req: BlankSessionRequest):
    """
    Start a session from a schema instead of a file: the schema is the source of
    truth, the data comes later (or never). Columns are given explicitly, or
    seeded from a library artefact — a config brings its validation rules along,
    so it can be tried on hand-typed rows without fabricating a CSV.
    """
    seeded_fields: dict | None = None
    if req.artefact_id:
        with session_scope() as s:
            try:
                ver = repo.resolve_ref(s, req.artefact_id, req.artefact_version)
            except repo.NotFound as e:
                raise HTTPException(404, str(e))
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
    sid = store.create(df, file_type="MANUAL", encoding="N/A", delimiter="N/A")

    n = max(0, min(int(req.rows), 500))
    if n:
        sess = store.get(sid)
        for _ in range(n):
            idx = sess.new_index()
            sess.work_df.loc[idx] = {c: "" for c in columns}
            sess.added.add(idx)

    return FileResponse(
        session_id=sid, type="MANUAL", encoding="N/A", delimiter="N/A",
        preview=_preview(store.get(sid).active_df(), 500),
        seeded_fields=seeded_fields or None,
    )


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
            out[col] = [{"value": _crypto.MASK, "count": v.get("count", 0)}
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
def preview_active(sid: str, limit: int = 150):
    """The working table as it stands — edits applied, deleted rows excluded.
    What the client reloads after adding or removing rows."""
    sess = _session(sid)
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
    sess = _session(sid)
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


@app.post("/api/files/{sid}/rows/add", response_model=RowsMutationResponse)
def add_rows(sid: str, req: AddRowsRequest,
        _cap=Depends(require_capability("file.edit_cells"))):
    """
    Append blank rows, or duplicate an existing one. New indices come from a
    monotonic counter and are never reused: the row index is the stable key the
    edit overlay, the report and the dataset writer all rely on.
    """
    sess = _session(sid)
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
        _cap=Depends(require_capability("file.edit_cells"))):
    """
    Logical deletion: rows leave the active table but stay in `work_df`, so
    /cells/reset brings them back and the count is reportable. `all_filtered`
    deletes everything matching the current filters and/or statuses of the last
    run — the whole point of 'filter the errors, drop the batch'.
    """
    sess = _session(sid)
    targets: set[int] = {i for i in req.indices if i in sess.work_df.index}

    if req.all_filtered:
        if sess.last_df is None:
            raise HTTPException(409, "Run validation first to delete a filtered batch.")
        cols = [c for c in (sess.last_cols or []) if c in sess.last_df.columns]
        df = sess.last_df[cols] if cols else sess.last_df.head(0)
        if req.filters:
            df = _apply_filters(df, req.filters)
        if req.statuses:
            wanted = {s.upper() for s in req.statuses}
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
def restore_rows(sid: str, req: DeleteRowsRequest):
    """Undo a logical deletion — all of it, or the given indices."""
    sess = _session(sid)
    if req.indices:
        back = {i for i in req.indices if i in sess.deleted}
    else:
        back = set(sess.deleted)
    sess.deleted -= back
    return _rows_state(sess, restored=len(back))


@app.post("/api/files/{sid}/rows/reorder", response_model=TablePreview)
def reorder_row(sid: str, req: ReorderRowRequest, preview_limit: int = 500):
    """
    Move one row to sit right after another (or to the very start). A real
    permutation of `work_df`'s row order — not a display trick — so the new
    order survives export and writing to a table, exactly like a hand-typed
    or reloaded file would read back in whatever order its rows were in.
    """
    sess = _session(sid)
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
):
    sess = _session(sid)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty TCO file.")
    try:
        delim = None if delimiter in ("AUTO", "") else delimiter
        tco_df = _tco.load_tco(raw, delimiter=delim, encoding=encoding)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e))
    sess.tco_df = tco_df
    return TcoResponse(rows=int(len(tco_df)), labels=_tco.get_available_labels(tco_df))


@app.post("/api/files/{sid}/process", response_model=ProcessResponse)
def process(sid: str, req: ProcessRequest,
        _cap=Depends(require_capability("file.process"))):
    sess = _session(sid)
    fields: dict[str, FieldConfig] = req.fields
    # One or more identifier fields (every field marked as identifier).
    id_fields = [c for c in req.visible_cols
                 if fields.get(c) and getattr(fields[c], "identifiant", False)]
    # Known *before* the SQL step runs: a cross-source query must never see a
    # declared-confidential column's real value, only its mask.
    declared_sensitive = frozenset(c for c, f in fields.items() if getattr(f, "sensitive", None))

    try:
        result = _process.run_pipeline(
            df_edited=sess.active_df(),
            visible_cols=req.visible_cols,
            field_configs=fields,
            tco_df=sess.tco_df,
            identifier_fields=id_fields,
            computed=[(c.name, c.expression) for c in req.computed],
            sql_computed=[(c.name, c.expression) for c in req.sql_computed],
            attached=sess.attached,
            sensitive_cols=declared_sensitive,
            report_flagged_only=True,
            variables=req.variables,
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
        sess.last_report = result["report"]                       # full report for /report
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

        data, status, row_index = [], [], []
        for idx, row in zip(head.index, head.itertuples(index=False, name=None)):
            row_index.append(int(idx))
            data.append([_cell(v) for v in row])
            row_status = []
            for col in cols:
                if col in computed_names:
                    row_status.append("COMPUTED")
                    continue
                v_series = validation.get(col)
                res = v_series.get(idx, "OK") if v_series is not None else "OK"
                clean = bool(clean_mask.get(col, pd.Series(dtype=bool)).get(idx, False))
                row_status.append(_status_of(str(res), clean))
            status.append(row_status)

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

    # Mask before the response is built, not in each consumer: the screen, the
    # report and anything reading this payload get the same protected view.
    if sensitivity:
        _sens_idx = [i for i, c in enumerate(cols) if c in sensitivity]
        if _sens_idx:
            data = [[_crypto.MASK if (i in _sens_idx and v) else v
                     for i, v in enumerate(row)] for row in data]
            for r in report_rows:
                if getattr(r, "colonne", None) in sensitivity:
                    if getattr(r, "valeur_finale", None):
                        r.valeur_finale = _crypto.MASK
                    if getattr(r, "valeur_source", None):
                        r.valeur_source = _crypto.MASK

    return ProcessResponse(
        columns=cols,
        data=data,
        status=status,
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
):
    """
    A page of the last processed table, with per-cell status. Filtering and
    sorting apply to the WHOLE file (not the on-screen sample), so the returned
    `total` is the true filtered count out of `total_all`.
    """
    sess = _session(sid)
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

    data, status, row_index = [], [], []
    for idx, row in zip(page.index, page.itertuples(index=False, name=None)):
        row_index.append(int(idx))
        data.append([_cell(v) for v in row])
        row_status = []
        for col in cols:
            if col in computed:
                row_status.append("COMPUTED")
                continue
            v_series = validation.get(col)
            res = v_series.get(idx, "OK") if v_series is not None else "OK"
            clean = bool(clean_mask.get(col, pd.Series(dtype=bool)).get(idx, False))
            row_status.append(_status_of(str(res), clean))
        status.append(row_status)

    return RowsResponse(
        columns=cols, data=data, status=status,
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
):
    """
    Flexible report generation over the last run. The same data can be shaped as:
      • long   — one row per flagged cell (id, column, original, final, status, message)
      • by_id  — grouped per id: [{id, errors:[{column, value, status, message}]}]
      • pivot  — one row per id, one column per field (cell = status / message / final)
    Filter by `statuses` and `columns`. Output as json (preview), csv or xlsx.
    """
    sess = _session(sid)
    rep = sess.last_report
    if rep is None:
        raise HTTPException(409, "Run validation first to build a report.")

    df = rep.copy()
    want = [s.strip().upper() for s in statuses.split(",") if s.strip()]
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
):
    """
    Export the processed table (or the working table if nothing has been run
    yet) as CSV or XLSX, with a chosen encoding and delimiter.

    `filters` is an optional JSON object {column: text}; when given, only rows
    where every listed column contains its text (case-insensitive) are exported.
    Filtering applies to the WHOLE file, not just the on-screen sample.
    """
    sess = _session(sid)
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
def drop(sid: str):
    store.drop(sid)
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
def _session(sid: str):
    try:
        return store.get(sid)
    except KeyError:
        raise HTTPException(404, "Session not found or expired. Re-upload the file.")
