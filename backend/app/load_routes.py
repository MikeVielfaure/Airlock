"""
Loading a file into a target that already exists.

The pipeline could clean and validate anything — but only against a
configuration someone wrote for that file. So loading a partner's list into a
correspondence table meant renaming columns in Excel first, and skipping the
checks entirely.

The idea here: **the target's schema replaces the configuration**. A
correspondence table declares TYPE / SOURCE_VALUE / TARGET_LABEL; a stored table
records its columns, types and key. That is everything a validation needs, so
loading into an existing target requires no config at all — just a mapping from
the file's columns onto the target's, which is proposed automatically and
corrected by hand.

Same machinery as everywhere else, reached from one more place: the cleaning
engine does the cleaning, the field rules do the checking, and a load is refused
before it starts rather than half-applied.
"""
from __future__ import annotations

import io
import re
import unicodedata
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repository as repo
from app.auth_routes import current_env, require_capability
from app.db import commit, get_session
from app.db_models import Dataset
from app.models import FieldConfig
from app.services import dataset_service as ds
from app.services.process_service import ProcessService
from app.session import store

router = APIRouter(prefix="/api/targets", tags=["load"])

# The correspondence table is not a free-form table: its shape is fixed, which
# is exactly what lets a file be loaded into it without a configuration.
TCO_SCHEMA = {
    "columns": ["TYPE", "SOURCE_VALUE", "TARGET_LABEL"],
    "types": {"TYPE": "string", "SOURCE_VALUE": "string", "TARGET_LABEL": "string"},
    "key": ["TYPE", "SOURCE_VALUE"],
    "required": ["SOURCE_VALUE", "TARGET_LABEL"],
}


def _norm(name: str) -> str:
    """Fold a header for comparison: accents, case and separators removed. A
    partner writing 'Libellé cible' and a schema saying TARGET_LABEL should meet
    without anyone typing a mapping by hand."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ── describing what can be loaded into ────────────────────────────────
def _dataset_schema(d: Dataset) -> dict:
    sch = d.schema_json or {}
    return {"columns": list(sch.get("columns", [])),
            "types": dict(sch.get("types", {})),
            "key": list(sch.get("key", [])),
            "required": []}


@router.get("")
def list_targets(scope: str = Depends(current_env),
                 _cap=Depends(require_capability("dataset.read")),
                 s: Session = Depends(get_session)):
    """Everything a file can be loaded into, with the schema each one imposes."""
    out: List[dict] = []
    for a in repo.list_artefacts(s, "tco", environment=scope):
        out.append({"kind": "tco", "id": a.id, "name": a.name,
                    "version_no": a.latest_version_no, "schema": TCO_SCHEMA})
    q = select(Dataset).where(Dataset.archived.is_(False))
    if scope != "*":
        q = q.where(Dataset.environment == scope)
    for d in s.scalars(q):
        out.append({"kind": "dataset", "id": d.id, "name": d.name,
                    "row_count": d.row_count, "schema": _dataset_schema(d)})
    return out


def _target_schema(s: Session, kind: str, target_id: str, scope: str) -> tuple[dict, Any]:
    if kind == "tco":
        try:
            art = repo.get_artefact(s, target_id)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        if art.kind != "tco":
            raise HTTPException(409, f"'{art.name}' is a {art.kind}, not a tco.")
        return dict(TCO_SCHEMA), art
    if kind == "dataset":
        try:
            d = repo.get_dataset(s, target_id)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        return _dataset_schema(d), d
    raise HTTPException(422, f"Unknown target kind '{kind}'.")


# ── proposing the column mapping ──────────────────────────────────────
class SuggestIn(BaseModel):
    session_id: str


@router.post("/{kind}/{target_id}/suggest")
def suggest_mapping(kind: str, target_id: str, req: SuggestIn,
                    scope: str = Depends(current_env),
                    _cap=Depends(require_capability("dataset.read")),
                    s: Session = Depends(get_session)):
    """
    Propose which file column feeds which target column.

    Matching is on folded names, so accents and separators do not defeat it. It
    is a proposal, not a decision: what is not matched is reported so the person
    completes it rather than discovering the gap after the load.
    """
    try:
        sess = store.get(s, req.session_id)
    except KeyError:
        raise HTTPException(404, f"Session {req.session_id} not found.")
    schema, _ = _target_schema(s, kind, target_id, scope)

    file_cols = [str(c) for c in sess.active_df().columns]
    folded = {_norm(c): c for c in file_cols}
    mapping: Dict[str, str] = {}
    for target_col in schema["columns"]:
        hit = folded.get(_norm(target_col))
        if hit:
            mapping[target_col] = hit
    unmatched_target = [c for c in schema["columns"] if c not in mapping]
    unused_file = [c for c in file_cols if c not in mapping.values()]
    return {"mapping": mapping, "file_columns": file_cols,
            "target_columns": schema["columns"],
            "unmatched_target": unmatched_target, "unused_file": unused_file,
            "schema": schema}


# ── preview and load ──────────────────────────────────────────────────
class ComputedIn(BaseModel):
    name: str
    expression: str


class LoadIn(BaseModel):
    """
    A load has *two* layers, and conflating them was a mistake worth naming.

    The **inbound configuration** describes the treatment: clean the file,
    enforce business rules, derive columns from other columns. It is the same
    configuration object used everywhere else — inline, or taken from the
    library — because a partner's file needs the same care whether it ends up in
    a table or in an export.

    The **target schema** is the last word: shape, types, required columns, key.
    It cannot express a date format or a computed column, so it can never be the
    specification — it is the guarantee that whatever the treatment produced
    still fits. A database constraint is a safety net, not a design.
    """
    session_id: str
    # target column -> source column (a file column, or one computed below).
    mapping: Dict[str, str] = Field(default_factory=dict)
    mode: str = "append"                    # append | replace
    policy: str = "reject"                  # reject | block | all

    # ── inbound configuration ────────────────────────────────────────
    config_artefact_id: str = ""            # a stored config…
    config_version_no: Optional[int] = None
    fields: Dict[str, Dict[str, Any]] = Field(default_factory=dict)   # …or inline
    # Columns derived from other columns, in declaration order so one may build
    # on the previous. These become mappable like any other column.
    computed: List[ComputedIn] = Field(default_factory=list)

    # ── on top of the target schema ──────────────────────────────────
    rules: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


def _prepare(s: Session, kind: str, target_id: str, scope: str, req: LoadIn):
    """Build the frame that would land, and everything wrong with it."""
    try:
        sess = store.get(s, req.session_id)
    except KeyError:
        raise HTTPException(404, f"Session {req.session_id} not found.")
    schema, target = _target_schema(s, kind, target_id, scope)

    src = sess.active_df().copy()

    # ── 1. inbound configuration: clean, enforce, derive ─────────────
    inbound_problems: List[dict] = []
    inbound_bad: set = set()
    field_specs = dict(req.fields)
    if req.config_artefact_id:
        try:
            body = repo.resolve_ref(s, req.config_artefact_id,
                                    req.config_version_no).body or {}
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        for f in (body.get("Fields") or []):
            names = f.get("name") or []
            col = names[0] if names else None
            if col:
                field_specs.setdefault(col, f)

    if field_specs:
        in_fields = []
        for col, spec in field_specs.items():
            rule = dict(spec)
            rule.setdefault("name", [col])
            try:
                in_fields.append(FieldConfig(**rule))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, f"Unreadable inbound rule for '{col}': {e}")
        src, in_validation, _w = ProcessService().apply_field_configs(src, in_fields)
        for col, status in in_validation.items():
            for pos, st in enumerate(status.tolist()):
                label = str(st).strip()
                if label and label.upper() != "OK":
                    inbound_bad.add(pos)
                    if len(inbound_problems) < 200:
                        inbound_problems.append(
                            {"row": pos + 1, "column": col, "stage": "entrée",
                             "value": str(src.iloc[pos].get(col, "")),
                             "message": label})

    if req.computed:
        from app.services.compute_service import ComputeService
        engine = ComputeService()
        for c in req.computed:
            try:
                src[c.name] = engine.evaluate(src, c.expression)
            except Exception as e:  # noqa: BLE001 — name the column, not a trace
                raise HTTPException(422, f"Expression for '{c.name}': {e}")
    # Name the *file* column, not the target one: that is the name the person
    # typed and the only one they can go and check.
    missing_cols = sorted({f for f in req.mapping.values() if f not in src.columns})
    if missing_cols:
        raise HTTPException(422, f"Column(s) absent from the file: "
                                 f"{', '.join(missing_cols)}")

    # Confidential columns must not be laundered into a shared table by way of
    # a load that never saw the configuration marking them.
    sensitivity = dict(getattr(sess, "sensitivity", {}) or {})
    leaking = sorted({t for t, f in req.mapping.items() if f in sensitivity})
    if leaking:
        raise HTTPException(
            409, f"Column(s) {', '.join(leaking)} are confidential and cannot be "
                 f"loaded into a shared table this way.")

    # ── 2. mapping onto the target's shape ───────────────────────────
    out = pd.DataFrame(index=src.index)
    for target_col in schema["columns"]:
        file_col = req.mapping.get(target_col)
        out[target_col] = src[file_col].astype("string").fillna("") if file_col else ""

    # The schema *is* the configuration: types come from the target, and the
    # required columns of a fixed-shape target become nullable: false.
    fields = []
    for col in schema["columns"]:
        rule = {"name": [col], "type": schema["types"].get(col, "string")}
        if col in (schema.get("required") or []):
            rule["nullable"] = False
        rule.update(req.rules.get(col, {}))
        try:
            fields.append(FieldConfig(**rule))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Unreadable rule for '{col}': {e}")

    # ── 3. the target has the last word ──────────────────────────────
    cleaned, validation, _warn = ProcessService().apply_field_configs(out, fields)

    problems: List[dict] = list(inbound_problems)
    bad_rows: set = set(inbound_bad)
    for col, status in validation.items():
        for pos, st in enumerate(status.tolist()):
            label = str(st).strip()
            if label and label.upper() != "OK":
                bad_rows.add(pos)
                if len(problems) < 400:
                    problems.append({"row": pos + 1, "column": col, "stage": "cible",
                                     "value": str(cleaned.iloc[pos].get(col, "")),
                                     "message": label})

    # A duplicate key silently overwrites a correspondence: say so before it
    # happens rather than after someone notices a mapping changed by itself.
    dupes = 0
    key = [k for k in (schema.get("key") or []) if k in cleaned.columns]
    if key:
        dupes = int(cleaned.duplicated(subset=key, keep="last").sum())

    return sess, schema, target, cleaned, problems, bad_rows, dupes


def _verdict(cleaned, problems, bad_rows, dupes, req) -> dict:
    total = int(len(cleaned))
    rejected = len(bad_rows) if req.policy == "reject" else 0
    return {"rows_in": total,
            "rows_to_load": total - rejected if req.policy != "block" or not bad_rows
                            else 0,
            "rows_rejected": rejected,
            "duplicate_keys": dupes,
            "blocked": bool(bad_rows) and req.policy == "block",
            "problems": problems}


@router.post("/{kind}/{target_id}/preview")
def preview_load(kind: str, target_id: str, req: LoadIn,
                 scope: str = Depends(current_env),
                 _cap=Depends(require_capability("dataset.read")),
                 s: Session = Depends(get_session)):
    """What would land, and what is wrong with it — without touching anything."""
    _sess, _schema, _t, cleaned, problems, bad, dupes = _prepare(
        s, kind, target_id, scope, req)
    head = cleaned.head(50)
    return {**_verdict(cleaned, problems, bad, dupes, req),
            "preview": {"columns": list(cleaned.columns),
                        "data": head.astype(str).values.tolist(),
                        "total_rows": int(len(cleaned)),
                        "shown_rows": int(len(head))}}


@router.post("/{kind}/{target_id}/load")
def load(kind: str, target_id: str, req: LoadIn,
         scope: str = Depends(current_env),
         _cap=Depends(require_capability("tco.append")),
         s: Session = Depends(get_session)):
    """
    Load for real.

    Refused wholesale when the policy blocks: a half-applied load into a shared
    table is worse than none, because nobody can tell which half.
    """
    _sess, schema, target, cleaned, problems, bad, dupes = _prepare(
        s, kind, target_id, scope, req)
    if bad and req.policy == "block":
        raise HTTPException(422, f"{len(bad)} row(s) fail the target's rules — "
                                 f"nothing was loaded.")
    if req.policy == "reject" and bad:
        cleaned = cleaned.drop(cleaned.index[sorted(bad)])

    if kind == "tco":
        existing = ""
        if req.mode == "append":
            existing = (repo.resolve_ref(s, target.id, None).body or {}).get("csv", "")
        old = (pd.read_csv(io.StringIO(existing), sep=";", dtype=str)
               if existing.strip() else pd.DataFrame())
        merged = pd.concat([old, cleaned], ignore_index=True) if len(old) else cleaned
        key = [k for k in schema["key"] if k in merged.columns]
        if key:
            merged = merged.drop_duplicates(subset=key, keep="last")
        # A new version, never in place: the run that used the previous table
        # stays reproducible.
        ver = repo.add_version(s, target.id, {"csv": merged.fillna("").to_csv(sep=";", index=False)},
                               note=f"import de {len(cleaned)} ligne(s)")
        commit(s)
        return {"target": "tco", "name": target.name, "version_no": ver.version_no,
                "rows_loaded": int(len(cleaned)), "rows_total": int(len(merged)),
                "rows_rejected": len(bad) if req.policy == "reject" else 0,
                "duplicate_keys": dupes}

    # dataset
    if req.mode == "replace":
        repo.delete_all_rows(s, target.id)
    key_fields = [k for k in schema.get("key", []) if k in cleaned.columns]
    payload = ds.rows_payload(cleaned, key_fields=key_fields,
                              error_rows=set(), policy="all")
    written = repo.insert_rows(s, target.id, payload)
    target.row_count = repo.count_rows(s, target.id)
    commit(s)
    return {"target": "dataset", "name": target.name, "rows_loaded": written,
            "rows_total": target.row_count,
            "rows_rejected": len(bad) if req.policy == "reject" else 0,
            "duplicate_keys": dupes}
