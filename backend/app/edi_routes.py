"""
edi_routes.py
─────────────
HTTP layer of the EDI module. Thin: parse the request, resolve the model
(inline YAML or a stored `edi_model` artefact by id), call the engine,
shape the response. All decisions live in services/edi_service.py.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import repository as repo
from app.auth_routes import require_capability, require_user
from app.db import get_session
from app.db_models import User
from app.edi_models import EdiModel, model_from_yaml, model_to_yaml
from app.models import FileResponse, TablePreview
from app.services import edi_kb, edi_service
from app.services.edi_lexer import EdiSyntaxError, detect_format, parse
from app.services.file_service import FileService

router = APIRouter(prefix="/api/edi", tags=["edi"])
_files = FileService()

_ENCODINGS = ("utf-8", "latin-1", "cp1252")


def _read_text(raw: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _lex_or_422(raw: bytes):
    text = _read_text(raw)
    fmt = detect_format(text)
    if fmt == "X12":
        raise HTTPException(422, "Fichier ANSI X12 détecté (segment ISA). Cette version traite l'EDIFACT ; X12 est prévu ensuite.")
    if fmt != "EDIFACT":
        raise HTTPException(422, "Ce fichier ne ressemble pas à de l'EDIFACT (ni UNA ni UNB en tête). Le format se détecte au contenu, pas à l'extension.")
    try:
        seps, segments, had_una = parse(text)
    except EdiSyntaxError as e:
        raise HTTPException(422, f"Erreur de syntaxe EDI : {e}")
    return seps, segments, had_una


def _resolve_model(s: Session, model_yaml: Optional[str], model_id: Optional[str],
                   model_version: Optional[int], user: Optional[User] = None) -> EdiModel:
    if model_yaml and model_yaml.strip():
        try:
            return model_from_yaml(model_yaml)
        except ValueError as e:
            raise HTTPException(422, f"Modèle EDI invalide : {e}")
    if model_id:
        from app.store_routes import _user_can_view_artefact
        try:
            art = repo.get_artefact(s, model_id)
            # 404, not 409/403, and checked before anything else is revealed
            # about it (even its kind): an artefact from an environment this
            # caller cannot see must not even confirm it exists — same rule
            # as the generic artefact routes (store_routes.py).
            if user is not None and not _user_can_view_artefact(s, user, art):
                raise HTTPException(404, f"Modèle {model_id} introuvable.")
            if art.kind != "edi_model":
                raise HTTPException(409, f"L'artefact '{art.name}' est de type {art.kind}, pas edi_model.")
            ver = repo.resolve_ref(s, model_id, model_version)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        try:
            return EdiModel(**ver.body)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Le modèle stocké est illisible : {e}")
    raise HTTPException(422, "Fournir un modèle : `model_yaml` ou `model_id` (+ `model_version`).")


def _preview(df: pd.DataFrame, limit: int = 60) -> TablePreview:
    head = df.head(limit)
    return TablePreview(
        columns=[str(c) for c in df.columns],
        data=[[str(v) for v in row] for row in head.itertuples(index=False, name=None)],
        total_rows=int(len(df)), shown_rows=int(len(head)),
        index=[int(i) for i in head.index])


def _download(filename: str, data: bytes, media: str) -> dict:
    return {"filename": filename, "media_type": media,
            "content_base64": base64.b64encode(data).decode("ascii")}


# ── knowledge base (feeds the in-app doc) ─────────────────────────────
@router.get("/kb")
def knowledge_base():
    return edi_kb.kb_payload()


# ── inspect: detect + decode + free syntax checks ─────────────────────
@router.post("/inspect")
async def inspect(file: UploadFile = File(...),
                  _cap=Depends(require_capability("file.upload"))):
    seps, segments, had_una = _lex_or_422(await file.read())
    inters, errors = edi_service.split_interchanges(segments)
    tree = edi_service.decode_tree(seps, inters)
    return {"format": "EDIFACT", "had_una": had_una, "filename": file.filename,
            "syntax_errors": errors, **tree}


# ── infer a model skeleton from a sample file ─────────────────────────
@router.post("/models/infer")
async def infer(file: UploadFile = File(...), name: str = Form(""),
                _cap=Depends(require_capability("file.upload"))):
    seps, segments, _ = _lex_or_422(await file.read())
    inters, _errs = edi_service.split_interchanges(segments)
    try:
        model, notes = edi_service.infer_model(seps, inters, name=name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"yaml": model_to_yaml(model), "model": model.model_dump(), "notes": notes}


# ── validate a file against a model ───────────────────────────────────
@router.post("/validate")
async def validate(file: UploadFile = File(...),
                   model_yaml: Optional[str] = Form(None),
                   model_id: Optional[str] = Form(None),
                   model_version: Optional[int] = Form(None),
                   user: User = Depends(require_capability("file.upload")),
                   s: Session = Depends(get_session)):
    model = _resolve_model(s, model_yaml, model_id, model_version, user)
    seps, segments, _ = _lex_or_422(await file.read())
    inters, syntax_errors = edi_service.split_interchanges(segments)
    errors, stats = edi_service.validate(seps, inters, model)
    return {"model_name": model.name, "syntax_errors": syntax_errors,
            "model_errors": errors, "stats": stats,
            "ok": not syntax_errors and not errors}


# ── pivot: EDI -> flat/linked tables (preview | csv | xlsx | session) ─
@router.post("/pivot")
async def pivot(file: UploadFile = File(...),
                mode: str = Form("flat"),
                target: str = Form("preview"),
                model_yaml: Optional[str] = Form(None),
                model_id: Optional[str] = Form(None),
                model_version: Optional[int] = Form(None),
                user: User = Depends(require_capability("file.upload")),
                s: Session = Depends(get_session)):
    if mode not in ("flat", "linked"):
        raise HTTPException(422, "mode doit être 'flat' ou 'linked'.")
    if target not in ("preview", "csv", "xlsx", "session"):
        raise HTTPException(422, "target doit être preview, csv, xlsx ou session.")
    model = _resolve_model(s, model_yaml, model_id, model_version, user)
    seps, segments, _ = _lex_or_422(await file.read())
    inters, _errs = edi_service.split_interchanges(segments)
    records = edi_service.extract_records(inters, model)
    if not records:
        raise HTTPException(422, "Aucun message exploitable dans le fichier.")
    piv = edi_service.pivot(records, model, mode)
    base = (file.filename or "edi").rsplit(".", 1)[0]

    # `extract_records` is deliberately best-effort — a segment or qualifier
    # the model doesn't expect is silently skipped rather than blocking the
    # pivot. Silent is the problem: without this, nothing tells the operator
    # that data was left out. `validate()` already computes exactly this
    # (unexpected segments, missing mandatory ones, unknown qualifiers) at
    # no extra parsing cost, so every pivot target carries it alongside the
    # result instead of only the separate, rarely-used /validate endpoint.
    model_errors, model_stats = edi_service.validate(seps, inters, model)

    if target == "session":
        flat = piv.get("flat")
        if flat is None:
            flat = edi_service.pivot(records, model, "flat")["flat"]
        from app.session import store
        sid = store.create(s, flat, file_type="CSV", encoding="utf-8", delimiter=";")
        return FileResponse(session_id=sid, type="CSV", encoding="utf-8",
                            delimiter=";", sheet=None, sheets=[], table_count=0,
                            preview=_preview(flat)).model_dump() | {
            "model_errors": model_errors, "model_stats": model_stats}

    if target == "preview":
        if mode == "flat":
            return {"mode": "flat", "flat": _preview(piv["flat"]),
                    "model_errors": model_errors, "model_stats": model_stats}
        return {"mode": "linked", "heads": _preview(piv["heads"]),
                "items": _preview(piv["items"]),
                "model_errors": model_errors, "model_stats": model_stats}

    frames = ({"flat": piv["flat"]} if mode == "flat"
              else {"heads": piv["heads"], "items": piv["items"]})
    if target == "csv":
        if mode == "flat":
            data = piv["flat"].to_csv(sep=";", index=False).encode("utf-8-sig")
            return {"files": [_download(f"{base}_pivot.csv", data, "text/csv")],
                    "model_errors": model_errors, "model_stats": model_stats}
        return {"files": [
            _download(f"{base}_{n}.csv", f.to_csv(sep=";", index=False).encode("utf-8-sig"), "text/csv")
            for n, f in frames.items()], "model_errors": model_errors, "model_stats": model_stats}
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for n, f in frames.items():
            f.to_excel(xw, sheet_name=n, index=False)
    return {"files": [_download(f"{base}_pivot.xlsx", buf.getvalue(),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            "model_errors": model_errors, "model_stats": model_stats}


# ── generate: flat CSV/XLSX -> EDIFACT ────────────────────────────────
@router.post("/generate")
async def generate(file: UploadFile = File(...),
                   model_yaml: Optional[str] = Form(None),
                   model_id: Optional[str] = Form(None),
                   model_version: Optional[int] = Form(None),
                   group_by: str = Form(""),
                   sender: str = Form(""),
                   recipient: str = Form(""),
                   interchange_ref: str = Form(""),
                   user: User = Depends(require_capability("file.upload")),
                   s: Session = Depends(get_session)):
    model = _resolve_model(s, model_yaml, model_id, model_version, user)
    raw = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            df = _files.load_xlsx_raw(raw, sheet=0)
        else:
            df, _enc, _delim = _files.load_csv_raw(raw, "AUTO", None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Fichier tabulaire illisible : {e}")
    df.columns = [str(c) for c in df.columns]
    known = set(sum(model.field_names().values(), []))
    if not (set(map(str, df.columns)) & known):
        raise HTTPException(422, "Aucune colonne du fichier ne correspond aux champs du modèle "
                                 f"({', '.join(sorted(known)[:8])}…). Vérifie les en-têtes.")
    records = edi_service.records_from_flat(df, model, group_by=group_by)
    text = edi_service.generate(model, records, sender=sender, recipient=recipient,
                                interchange_ref=interchange_ref)
    fname = f"{model.message_type.lower()}_{len(records)}msg.edi"
    return {"messages": len(records),
            "items": sum(len(r["items"]) for r in records),
            "file": _download(fname, text.encode("utf-8"), "application/edifact"),
            "preview": text[:1500]}


# ── convert: EDI -> (pivot interne) -> EDI ────────────────────────────
@router.post("/convert")
async def convert(file: UploadFile = File(...),
                  source_yaml: Optional[str] = Form(None),
                  source_id: Optional[str] = Form(None),
                  source_version: Optional[int] = Form(None),
                  target_yaml: Optional[str] = Form(None),
                  target_id: Optional[str] = Form(None),
                  target_version: Optional[int] = Form(None),
                  mapping: str = Form(""),
                  sender: str = Form(""), recipient: str = Form(""),
                  user: User = Depends(require_capability("file.upload")),
                  s: Session = Depends(get_session)):
    source = _resolve_model(s, source_yaml, source_id, source_version, user)
    target = _resolve_model(s, target_yaml, target_id, target_version, user)
    map_dict = {}
    if mapping.strip():
        try:
            map_dict = json.loads(mapping)
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "`mapping` doit être un objet JSON {champ_cible: champ_source}.")
        # Test explicite plutôt qu'`assert` : `python -O` retire les assertions,
        # et un interpréteur optimisé laisserait alors passer une liste JSON
        # jusqu'à un `.items()` bien plus loin, sur une erreur d'appelant que
        # cette route existe justement pour nommer ici.
        if not isinstance(map_dict, dict):
            raise HTTPException(422, "`mapping` doit être un objet JSON {champ_cible: champ_source}.")
    seps, segments, _ = _lex_or_422(await file.read())
    inters, _errs = edi_service.split_interchanges(segments)
    text = edi_service.convert(inters, source, target, mapping=map_dict,
                               sender=sender, recipient=recipient)
    n_msg = sum(len(i.messages) for i in inters)
    fname = f"{target.message_type.lower()}_{target.directory.lower()}_converted.edi"
    return {"messages": n_msg, "source": source.name, "target": target.name,
            "file": _download(fname, text.encode("utf-8"), "application/edifact"),
            "preview": text[:1500]}


# ── stored edi_model rendered as YAML (for the editor) ────────────────
@router.get("/models/{artefact_id}/versions/{version_no}/yaml")
def model_yaml(artefact_id: str, version_no: int,
              user: User = Depends(require_user), s: Session = Depends(get_session)):
    from app.store_routes import _user_can_view_artefact
    try:
        art = repo.get_artefact(s, artefact_id)
        if not _user_can_view_artefact(s, user, art):
            raise HTTPException(404, f"Artefact {artefact_id} introuvable.")
        if art.kind != "edi_model":
            raise HTTPException(409, f"L'artefact '{art.name}' est de type {art.kind}, pas edi_model.")
        ver = repo.resolve_ref(s, artefact_id, version_no)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    return {"name": art.name, "version_no": ver.version_no,
            "yaml": model_to_yaml(EdiModel(**ver.body))}
