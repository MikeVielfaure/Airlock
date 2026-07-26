"""
Mapping and the universal pivot bridge.

Three things live here:
  1. thin helpers to resolve a mapping (inline YAML or a stored artefact),
  2. `POST /api/pivot/from/{source}` — turn any source into the canonical pivot
     object (the "special button": a source becomes a dataset anyone can use),
  3. `POST /api/pivot/convert` — source → pivot → target, the hub-and-spoke
     conversion that makes "anything ↔ anything" one code path.

Every route reads a source into pivot records and/or writes pivot records into a
target. Sources and targets are symmetric: flat (a working session, a CSV upload,
a stored dataset) and edi (an interchange + an EDI model). Adding a new kind of
source later means writing one reader and one writer — never a new pairwise
bridge.
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.mapping_models import Mapping, mapping_from_yaml
from app.services import edi_service, pivot_service
from app.services.file_service import FileService
from app.services.edi_lexer import parse as _lex_parse
from app.edi_models import EdiModel, model_from_yaml
from app.session import store

_files = FileService()

router = APIRouter(prefix="/api", tags=["pivot"])


# ── resolving a mapping ───────────────────────────────────────────────
def _resolve_mapping(s: Session, mapping_yaml: Optional[str],
                     mapping_id: Optional[str], mapping_version: Optional[int]) -> Mapping:
    if mapping_yaml and mapping_yaml.strip():
        try:
            return mapping_from_yaml(mapping_yaml)
        except ValueError as e:
            raise HTTPException(422, f"Invalid mapping: {e}")
    if mapping_id:
        try:
            art = repo.get_artefact(s, mapping_id)
            if art.kind != "mapping":
                raise HTTPException(409, f"Artefact '{art.name}' is a {art.kind}, not a mapping.")
            ver = repo.resolve_ref(s, mapping_id, mapping_version)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        try:
            return Mapping(**ver.body)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Stored mapping is unreadable: {e}")
    raise HTTPException(422, "Provide a mapping: `mapping_yaml` or `mapping_id`.")


def _resolve_edi_model(s: Session, model_yaml: Optional[str],
                       model_id: Optional[str], model_version: Optional[int]) -> EdiModel:
    if model_yaml and model_yaml.strip():
        try:
            return model_from_yaml(model_yaml)
        except ValueError as e:
            raise HTTPException(422, f"Invalid EDI model: {e}")
    if model_id:
        try:
            art = repo.get_artefact(s, model_id)
            if art.kind != "edi_model":
                raise HTTPException(409, f"Artefact '{art.name}' is a {art.kind}, not an edi_model.")
            ver = repo.resolve_ref(s, model_id, model_version)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        return EdiModel(**ver.body)
    raise HTTPException(422, "An EDI source/target needs `edi_model_yaml` or `edi_model_id`.")


# ── reading a source into pivot records ───────────────────────────────
def _read_flat_frame_from_session(sid: str) -> pd.DataFrame:
    try:
        sess = store.get(sid)
    except KeyError:
        raise HTTPException(404, f"Session {sid} not found.")
    return sess.active_df()


def _read_dataset_frame(s: Session, dataset_id: str) -> pd.DataFrame:
    try:
        ds = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    rows = repo.read_rows(s, dataset_id, offset=0, limit=1_000_000)
    cols = list((ds.schema_json or {}).get("columns", []))
    data = [r.data for r in rows]
    df = pd.DataFrame(data)
    if cols:
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
    return df.astype("string").fillna("")


# ── the pivot preview shape (matches TablePreview enough for the UI) ───
def _records_preview(records: list, limit: int = 100) -> dict:
    df = pivot_service.records_to_frame(records)
    head = df.head(max(1, limit))
    return {
        "columns": list(df.columns),
        "data": head.astype(str).values.tolist(),
        "total_rows": int(len(df)),
        "shown_rows": int(len(head)),
        "documents": len(records),
    }


# ══════════════════════════════════════════════════════════════════════
# 1. Any source → canonical pivot object
# ══════════════════════════════════════════════════════════════════════
@router.post("/pivot/from/{source}")
async def to_pivot_object(
    source: str,
    # flat sources
    session_id: str = Form(""),
    dataset_id: str = Form(""),
    file: Optional[UploadFile] = File(None),
    group_by: str = Form(""),
    # edi source
    edi_model_yaml: Optional[str] = Form(None),
    edi_model_id: Optional[str] = Form(None),
    edi_model_version: Optional[int] = Form(None),
    # the mapping (pivot ↔ source), and what to do with the result
    mapping_yaml: Optional[str] = Form(None),
    mapping_id: Optional[str] = Form(None),
    mapping_version: Optional[int] = Form(None),
    target: str = Form("preview"),          # preview | session
    s: Session = Depends(get_session),
):
    """
    Turn a source into the canonical pivot object. `source` is `flat` or `edi`.
    With `target=session` the pivot becomes an ordinary working table so the
    whole cleaning/validation machinery applies to it — the "convert to object"
    button.
    """
    mapping = _resolve_mapping(s, mapping_yaml, mapping_id, mapping_version)

    if source == "flat":
        if session_id:
            df = _read_flat_frame_from_session(session_id)
        elif dataset_id:
            df = _read_dataset_frame(s, dataset_id)
        elif file is not None:
            raw = await file.read()
            df, *_ = _files.load_csv_raw(raw, "AUTO", None)
        else:
            raise HTTPException(422, "A flat source needs session_id, dataset_id, or a file.")
        records = pivot_service.flat_to_pivot(df, mapping, group_by=group_by)

    elif source == "edi":
        if file is None:
            raise HTTPException(422, "An EDI source needs a file.")
        model = _resolve_edi_model(s, edi_model_yaml, edi_model_id, edi_model_version)
        raw = await file.read()
        try:
            seps, segments, _ = _lex_parse(raw.decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Could not parse EDI: {e}")
        inters, _errs = edi_service.split_interchanges(segments)
        edi_records = edi_service.extract_records(inters, model)
        records = pivot_service.edi_records_to_pivot(edi_records, mapping)

    else:
        raise HTTPException(422, f"Unknown source '{source}' (use 'flat' or 'edi').")

    checks = pivot_service.check_rules(records, mapping)

    if target == "session":
        df = pivot_service.records_to_frame(records)
        sid = store.create(df, file_type="PIVOT", encoding="N/A", delimiter="N/A")
        return {"session_id": sid, "documents": len(records),
                "preview": _records_preview(records), "checks": checks}

    return {"documents": len(records), "preview": _records_preview(records),
            "checks": checks}


# ══════════════════════════════════════════════════════════════════════
# 2. source → pivot → target  (anything ↔ anything)
# ══════════════════════════════════════════════════════════════════════
@router.post("/pivot/convert")
async def convert_through_pivot(
    source_kind: str = Form(...),           # flat | edi
    target_kind: str = Form(...),           # flat | edi
    # source inputs
    session_id: str = Form(""),
    dataset_id: str = Form(""),
    file: Optional[UploadFile] = File(None),
    group_by: str = Form(""),
    source_mapping_yaml: Optional[str] = Form(None),
    source_mapping_id: Optional[str] = Form(None),
    source_edi_model_yaml: Optional[str] = Form(None),
    source_edi_model_id: Optional[str] = Form(None),
    # target inputs
    target_mapping_yaml: Optional[str] = Form(None),
    target_mapping_id: Optional[str] = Form(None),
    target_edi_model_yaml: Optional[str] = Form(None),
    target_edi_model_id: Optional[str] = Form(None),
    sender: str = Form(""),
    recipient: str = Form(""),
    interchange_ref: str = Form(""),
    s: Session = Depends(get_session),
):
    """
    One code path for every conversion: read the source into pivot records with
    its mapping, then write those records to the target with the target's
    mapping. flat→edi, edi→flat, flat→flat, edi→edi are all the same two steps.
    """
    src_map = _resolve_mapping(s, source_mapping_yaml, source_mapping_id, None)

    # ---- read source → pivot records ----
    if source_kind == "flat":
        if session_id:
            df = _read_flat_frame_from_session(session_id)
        elif dataset_id:
            df = _read_dataset_frame(s, dataset_id)
        elif file is not None:
            raw = await file.read()
            df, *_ = _files.load_csv_raw(raw, "AUTO", None)
        else:
            raise HTTPException(422, "A flat source needs session_id, dataset_id, or a file.")
        records = pivot_service.flat_to_pivot(df, src_map, group_by=group_by)
    elif source_kind == "edi":
        if file is None:
            raise HTTPException(422, "An EDI source needs a file.")
        model = _resolve_edi_model(s, source_edi_model_yaml, source_edi_model_id, None)
        raw = await file.read()
        seps, segments, _ = _lex_parse(raw.decode("utf-8", errors="replace"))
        inters, _e = edi_service.split_interchanges(segments)
        records = pivot_service.edi_records_to_pivot(
            edi_service.extract_records(inters, model), src_map)
    else:
        raise HTTPException(422, f"Unknown source_kind '{source_kind}'.")

    checks = pivot_service.check_rules(records, src_map)
    tgt_map = _resolve_mapping(s, target_mapping_yaml, target_mapping_id, None)

    # ---- write pivot records → target ----
    if target_kind == "flat":
        out = pivot_service.pivot_to_flat(records, tgt_map)
        csv_text = out.to_csv(sep=";", index=False)
        return {"documents": len(records), "format": "csv", "checks": checks,
                "preview": _frame_preview(out),
                "content_base64": _b64(csv_text.encode("utf-8-sig")),
                "filename": "converti.csv", "media_type": "text/csv"}
    elif target_kind == "edi":
        model = _resolve_edi_model(s, target_edi_model_yaml, target_edi_model_id, None)
        edi_records = pivot_service.pivot_to_edi_records(records, tgt_map)
        text = edi_service.generate(model, edi_records, sender=sender,
                                    recipient=recipient, interchange_ref=interchange_ref)
        return {"documents": len(records), "format": "edi", "checks": checks,
                "preview": text[:2000],
                "content_base64": _b64(text.encode("utf-8")),
                "filename": "converti.edi", "media_type": "application/edifact"}
    else:
        raise HTTPException(422, f"Unknown target_kind '{target_kind}'.")


def _frame_preview(df: pd.DataFrame, limit: int = 100) -> dict:
    head = df.head(max(1, limit))
    return {"columns": list(df.columns),
            "data": head.astype(str).values.tolist(),
            "total_rows": int(len(df)), "shown_rows": int(len(head))}


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


# ── suggest a mapping skeleton from a source's fields ─────────────────
@router.post("/mapping/suggest")
async def suggest_mapping(
    source_kind: str = Form("flat"),
    session_id: str = Form(""),
    dataset_id: str = Form(""),
    file: Optional[UploadFile] = File(None),
    edi_model_yaml: Optional[str] = Form(None),
    edi_model_id: Optional[str] = Form(None),
    s: Session = Depends(get_session),
):
    """
    Propose a starting mapping: every source field linked to a pivot field of the
    same name. It is the identity mapping — the sensible default the user then
    edits, exactly like an inferred EDI model is a starting point, not a spec.
    """
    if source_kind == "edi":
        model = _resolve_edi_model(s, edi_model_yaml, edi_model_id, None)
        by_zone = model.field_names()
        links = []
        for name in by_zone.get("header", []) + by_zone.get("summary", []):
            links.append({"pivot": name, "source": name, "scope": "head"})
        for name in by_zone.get("items", []):
            links.append({"pivot": name, "source": name, "scope": "item"})
        m = Mapping(name=f"{model.name} ↔ pivot", source_kind="edi", links=links)
    else:
        if session_id:
            df = _read_flat_frame_from_session(session_id)
        elif dataset_id:
            df = _read_dataset_frame(s, dataset_id)
        elif file is not None:
            raw = await file.read()
            df, *_ = _files.load_csv_raw(raw, "AUTO", None)
        else:
            raise HTTPException(422, "A flat source needs session_id, dataset_id, or a file.")
        # First column that is constant is a natural head field; the rest repeat.
        links = []
        for c in df.columns:
            scope = "head" if df[c].nunique(dropna=False) <= 1 else "item"
            links.append({"pivot": str(c), "source": str(c), "scope": scope})
        m = Mapping(name="table ↔ pivot", source_kind="flat", links=links)

    from app.mapping_models import mapping_to_yaml
    return {"yaml": mapping_to_yaml(m), "mapping": m.model_dump(exclude_none=True),
            "fields": [l["pivot"] for l in links]}
