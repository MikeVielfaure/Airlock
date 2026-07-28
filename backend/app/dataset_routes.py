"""
dataset_routes.py
─────────────────
HTTP surface for datasets: send a cleaned session into the database instead of
downloading a file, read a table back, and browse the write log.

The important route is /preflight. It is a dry run that returns a verdict —
`ok`, or `blocked_by: "config" | "data"` with problems and hints. The frontend
calls it before showing the write button, so the user learns *why* a write
would fail while there is still something to do about it, and learns whether
the fix lives in the config or in the rows.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.auth_routes import require_capability, require_user
from app.models import FileResponse, TablePreview
from app.services import auth_service as _auth
from app.services import dataset_service as ds
from app.services import permissions as _perms
from app.db_models import User
from app.session import store

router = APIRouter(prefix="/api", tags=["datasets"])


# ── contracts ─────────────────────────────────────────────────────────
class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    columns: List[str] = Field(default_factory=list)
    # Which rows to write, by df index. Empty = every row that passes the policy.
    # This is what lets someone review in Data and send only what they approved,
    # one by one or in bulk — the machine proposes, the person decides.
    include_rows: List[int] = Field(default_factory=list)
    exclude_rows: List[int] = Field(default_factory=list)
    # Only for a table being created here.
    managed: bool = False
    key: List[str] = Field(default_factory=list)
    types: Dict[str, str] = Field(default_factory=dict)   # recorded from the config
    row_count: int = 0
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""


class WriteRequest(BaseModel):
    dataset_id: Optional[str] = None        # existing table…
    name: str = ""                          # …or a new one, by name
    description: str = ""
    mode: str = "replace"                   # replace | append | upsert
    policy: str = "reject"                  # reject | block | all
    key_fields: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    # Which rows to write, by df index. Empty = every row that passes the policy.
    # This is what lets someone review in Data and send only what they approved,
    # one by one or in bulk — the machine proposes, the person decides.
    include_rows: List[int] = Field(default_factory=list)
    exclude_rows: List[int] = Field(default_factory=list)
    # Only for a table being created here.
    managed: bool = False   # [] = the displayed ones
    source_name: str = ""


class WriteResponse(BaseModel):
    ok: bool
    blocked_by: Optional[str] = None
    problems: List[dict] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)
    dataset: Optional[DatasetInfo] = None
    rows_written: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    rows_deleted: int = 0
    write_id: Optional[str] = None


def _info(d, row_count: Optional[int] = None) -> DatasetInfo:
    sch = d.schema_json or {}
    return DatasetInfo(
        id=d.id, name=d.name, description=d.description,
        columns=list(sch.get("columns", [])), key=list(sch.get("key", [])),
        types=dict(sch.get("types", {})),
        row_count=d.row_count if row_count is None else row_count,
        archived=d.archived,
        created_at=d.created_at.isoformat() if d.created_at else "",
        updated_at=d.updated_at.isoformat() if d.updated_at else "")


# ══════════════════════════════════════════════════════════════════════
# Reading the session the same way the export does
# ══════════════════════════════════════════════════════════════════════
def _session_or_404(sid: str):
    try:
        return store.get(sid)
    except KeyError:
        raise HTTPException(404, "Session inconnue ou expirée.")


def _frame_and_errors(sess, columns: List[str]) -> tuple[pd.DataFrame, set, dict]:
    """
    The table to write, the indices of rows that failed validation, and the
    field types the config declared.

    A run is required: writing unvalidated data into a table would defeat the
    point of the tool.
    """
    if sess.last_df is None:
        raise HTTPException(409, "Lance la validation avant d'écrire en base : "
                                 "c'est elle qui distingue les lignes propres des autres.")
    cols = [c for c in (columns or sess.last_cols or []) if c in sess.last_df.columns]
    if not cols:
        raise HTTPException(422, "Aucune colonne à écrire.")
    df = sess.last_df[cols]

    error_rows: set = set()
    validation = sess.last_validation or {}
    computed = set(sess.last_computed or [])
    for col in cols:
        if col in computed:
            continue
        series = validation.get(col)
        if series is None:
            continue
        for idx, res in series.items():
            r = str(res)
            if r != "OK" and not r.startswith("MAPPING OK") and r != "NO_TCO":
                error_rows.add(idx)
    types = dict(sess.last_field_types or {})
    return df, error_rows, types


def _effective_key(req: WriteRequest, target) -> list:
    """
    The key to stamp on every row, whatever the mode.

    A row's `key_hash` is what a later upsert matches on, so it must be computed
    even when the current write does not merge: a table filled by `replace` with
    no stamped key cannot be upserted into afterwards — the second write finds
    nothing and duplicates everything. The request wins when it names a key,
    otherwise the key the dataset already recorded applies.
    """
    if req.key_fields:
        return list(req.key_fields)
    if target and target.schema_json:
        return list(target.schema_json.get("key", []))
    return []


def _run_preflight(sess, req: WriteRequest, s: Session):
    df, error_rows, types = _frame_and_errors(sess, req.columns)
    target = None
    if req.dataset_id:
        try:
            target = repo.get_dataset(s, req.dataset_id)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
    elif req.name.strip():
        target = repo.find_dataset_by_name(s, req.name)

    key_fields = _effective_key(req, target)
    row_count = repo.count_rows(s, target.id) if target else 0
    try:
        verdict = ds.preflight(
            df, mode=req.mode, key_fields=key_fields,
            dataset_schema=(target.schema_json if target else None),
            dataset_row_count=row_count, error_rows=error_rows,
            policy=req.policy, dataset_exists=target is not None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return df, error_rows, types, target, verdict, key_fields


# ══════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════
@router.get("/datasets")
def list_datasets(include_archived: bool = False, env: str = "",
                  user=Depends(require_user), s: Session = Depends(get_session)):
    """
    The tables this person can reach, each carrying what they may do with it.

    Filtering here rather than in the UI matters: a table someone cannot read
    should not appear in a list, or its very name leaks — and names are rarely
    neutral ("licenciements_2026").
    """
    scope = env or repo.DEFAULT_ENV
    role = _auth.role_in(s, user, scope) if getattr(user, "id", "") else "admin"
    out = []
    for d in repo.list_datasets(s, include_archived, environment=scope):
        perm = repo.dataset_permission(s, d, user, role, environment=scope)
        if not repo.can_on_dataset(perm, "read"):
            continue
        info = _info(d)
        payload = info.model_dump()
        payload["my_permission"] = perm
        payload["is_managed"] = d.is_managed
        payload["is_mine"] = bool(d.owner_id and d.owner_id == getattr(user, "id", ""))
        out.append(payload)
    return out


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo)
def get_dataset(dataset_id: str, s: Session = Depends(get_session)):
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    return _info(d, repo.count_rows(s, dataset_id))


@router.delete("/datasets/{dataset_id}")
def archive_dataset(dataset_id: str, s: Session = Depends(get_session),
        _cap=Depends(require_capability("dataset.delete"))):
    try:
        repo.archive_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return {"archived": dataset_id}


@router.get("/datasets/{dataset_id}/rows", response_model=TablePreview)
def read_dataset(dataset_id: str, offset: int = 0, limit: int = 100,
                 s: Session = Depends(get_session)):
    """Read the stored table back — the proof that what was written is what
    the user saw, and the starting point for a next round of corrections."""
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    limit = max(1, min(int(limit), 1000))
    rows = repo.read_rows(s, dataset_id, max(0, offset), limit)
    cols = list((d.schema_json or {}).get("columns", []))
    if not cols and rows:
        cols = list(rows[0].data.keys())
    total = repo.count_rows(s, dataset_id)
    return TablePreview(
        columns=cols,
        data=[[str(r.data.get(c, "")) for c in cols] for r in rows],
        total_rows=total, shown_rows=len(rows), index=list(range(offset, offset + len(rows))))


@router.get("/datasets/{dataset_id}/writes")
def dataset_writes(dataset_id: str, limit: int = 30, s: Session = Depends(get_session)):
    return [{"id": w.id, "mode": w.mode, "ok": w.ok, "blocked_by": w.blocked_by,
             "error": w.error, "problems": w.problems, "source_name": w.source_name,
             "rows_in": w.rows_in, "rows_written": w.rows_written,
             "rows_updated": w.rows_updated, "rows_rejected": w.rows_rejected,
             "rows_deleted": w.rows_deleted,
             "created_at": w.created_at.isoformat() if w.created_at else ""}
            for w in repo.list_writes(s, dataset_id, limit)]


@router.post("/files/{sid}/datasets/preflight", response_model=WriteResponse)
def preflight(sid: str, req: WriteRequest, s: Session = Depends(get_session)):
    """Dry run. Changes nothing, explains everything."""
    sess = _session_or_404(sid)
    _df, _err, _types, target, verdict, _key = _run_preflight(sess, req, s)
    return WriteResponse(
        ok=verdict["ok"], blocked_by=verdict["blocked_by"],
        problems=verdict["problems"], plan=verdict["plan"],
        dataset=_info(target, repo.count_rows(s, target.id)) if target else None)


@router.post("/files/{sid}/datasets/write", response_model=WriteResponse)
def write_dataset(sid: str, req: WriteRequest, env: str = "",
                  user=Depends(require_user), s: Session = Depends(get_session),
                  _cap=Depends(require_capability("dataset.write"))):
    """
    Write the session into a table. Runs preflight first and refuses on a
    blocking verdict — the refusal itself is logged, because "why did this not
    go through" is exactly the question asked a week later.
    """
    sess = _session_or_404(sid)
    df, error_rows, types, target, verdict, key_fields = _run_preflight(sess, req, s)
    # Writing into an *existing* table is decided by that table, not only by the
    # environment role: business tables and personal ones cannot share one rule.
    if target is not None and getattr(user, "id", ""):
        scope = env or getattr(target, "environment", "") or repo.DEFAULT_ENV
        perm = repo.dataset_permission(s, target, user, _auth.role_in(s, user, scope),
                                       environment=scope)
        if not repo.can_on_dataset(perm, "write"):
            raise HTTPException(
                403, f"Vous n'avez pas le droit d'écriture sur la table "
                     f"« {target.name} » (vous avez « {perm or 'aucun accès'} »).")
    name = (req.name.strip() or (target.name if target else ""))

    if not verdict["ok"]:
        w = repo.log_write(
            s, dataset_id=target.id if target else None, dataset_name=name,
            mode=req.mode, key_fields=key_fields, source_name=req.source_name,
            ok=False, blocked_by=verdict["blocked_by"],
            error=next((p["message"] for p in verdict["problems"]
                        if p["severity"] == "error"), "Écriture refusée."),
            problems=verdict["problems"], rows_in=int(len(df)))
        commit(s)                      # a refusal is a record, not a non-event
        return WriteResponse(ok=False, blocked_by=verdict["blocked_by"],
                             problems=verdict["problems"], plan=verdict["plan"],
                             dataset=_info(target) if target else None, write_id=w.id)

    # ── confidentiality ───────────────────────────────────────────────
    # Encrypt before anything is written. Doing it here rather than at each
    # call site means a new way of writing cannot forget: the frame that
    # reaches storage is already protected.
    sensitivity = dict(getattr(sess, "sensitivity", {}) or {})
    if sensitivity:
        from sqlalchemy import select as _select
        from app.db_models import CryptoKey as _CK
        from app.services import crypto_service as _cs
        alive = {k.name: k.wrapped_key for k in s.scalars(
            _select(_CK).where(_CK.name.in_(set(sensitivity.values())),
                               _CK.active.is_(True))) if k.wrapped_key}
        gone = sorted(set(sensitivity.values()) - set(alive))
        if gone:
            raise HTTPException(
                409, f"Key(s) {', '.join(gone)} no longer exist: this configuration "
                     f"cannot be used to write confidential columns.")
        # A confidential column cannot serve as a merge key: ciphertext differs
        # on every encryption, so the same person would never match themselves.
        clash = [c for c in key_fields if c in sensitivity]
        if clash:
            raise HTTPException(
                422, f"Column(s) {', '.join(clash)} are confidential and cannot be a "
                     f"merge key — encrypted values never compare equal.")
        try:
            df = _cs.encrypt_frame(df, sensitivity, alive)
        except _cs.CryptoUnavailable as e:
            raise HTTPException(503, str(e))

    # Row selection is applied *after* validation so a rejected row stays
    # rejected even if someone ticked it: approving cannot override a rule.
    if req.include_rows:
        keep = [i for i in req.include_rows if i in set(df.index)]
        df = df.loc[keep]
    if req.exclude_rows:
        df = df.drop(index=[i for i in req.exclude_rows if i in set(df.index)],
                     errors="ignore")

    payload = ds.rows_payload(df, key_fields=key_fields,
                              error_rows=error_rows, policy=req.policy)
    schema = ds.schema_of(df, key_fields, types)

    if target is None:
        if not name:
            raise HTTPException(422, "Donne un nom à la table à créer.")
        try:
            # Creating a table is its own right: not everyone who may write
            # into an existing one may invent new ones.
            if getattr(user, "id", "") and not _perms.can(
                    _auth.role_in(s, user, env or repo.DEFAULT_ENV),
                    "dataset.create"):
                raise HTTPException(
                    403, "« Créer une table » demande le rôle 'editor'.")
            target = repo.create_dataset(s, name, schema, req.description)
            target.owner_id = getattr(user, "id", "")
            target.is_managed = bool(req.managed)
        except repo.Conflict as e:
            raise HTTPException(409, str(e))
    elif req.mode == "replace":
        target.schema_json = schema        # replace is allowed to redefine

    deleted = updated = written = 0
    if req.mode == "replace":
        deleted = repo.delete_all_rows(s, target.id)
        written = repo.insert_rows(s, target.id, payload)
    elif req.mode == "append":
        written = repo.insert_rows(s, target.id, payload)
    else:                                   # upsert
        hashes = [r["key_hash"] for r in payload if r["key_hash"]]
        known = repo.existing_key_map(s, target.id, hashes)
        to_insert = [r for r in payload if not r["key_hash"] or r["key_hash"] not in known]
        to_update = [{"id": known[r["key_hash"]], "data": r["data"]}
                     for r in payload if r["key_hash"] and r["key_hash"] in known]
        written = repo.insert_rows(s, target.id, to_insert)
        updated = repo.update_rows(s, to_update)

    target.row_count = repo.count_rows(s, target.id)
    rejected = int(len(df)) - len(payload)
    w = repo.log_write(
        s, dataset_id=target.id, dataset_name=target.name, mode=req.mode,
        key_fields=key_fields, source_name=req.source_name, ok=True,
        problems=[p for p in verdict["problems"] if p["severity"] == "warning"],
        rows_in=int(len(df)), rows_written=written, rows_updated=updated,
        rows_rejected=rejected, rows_deleted=deleted)

    commit(s)
    return WriteResponse(
        ok=True, problems=[p for p in verdict["problems"] if p["severity"] == "warning"],
        plan=verdict["plan"], dataset=_info(target, target.row_count),
        rows_written=written, rows_updated=updated, rows_rejected=rejected,
        rows_deleted=deleted, write_id=w.id)


@router.get("/files/{sid}/source", response_model=TablePreview)
def source_preview(sid: str, limit: int = 100):
    """
    The file as it was actually read — before header treatment, renames and
    edits. When a write is blocked on the skeleton, this is what you look at:
    it shows whether the header landed on the right line and whether the
    delimiter split the columns the way you assumed.
    """
    sess = _session_or_404(sid)
    df = sess.raw_df
    limit = max(1, min(int(limit), 1000))
    head = df.head(limit)
    return TablePreview(
        columns=[str(c) for c in df.columns],
        data=[[("" if v is None else str(v)) for v in row]
              for row in head.itertuples(index=False, name=None)],
        total_rows=int(len(df)), shown_rows=int(len(head)),
        index=[int(i) for i in head.index])


def _table_preview(df: pd.DataFrame, limit: int = 500) -> TablePreview:
    head = df.head(max(1, limit))
    return TablePreview(columns=[str(c) for c in df.columns],
                        data=head.astype(str).values.tolist(),
                        total_rows=int(len(df)), shown_rows=int(len(head)),
                        index=[int(i) for i in head.index])


@router.post("/datasets/{dataset_id}/open", response_model=FileResponse)
def open_dataset(dataset_id: str, limit: int = 50_000, env: str = "",
                 user=Depends(require_user), s: Session = Depends(get_session)):
    """
    Open a stored table as an ordinary working session.

    Rather than building a second way to browse tables — with its own filters,
    its own sort, its own paging — a table simply *becomes* a session. Every
    mechanism that already exists then applies to it: the filters of the Data
    view, sorting, the editable mode, the report, the export, and writing the
    result back into a table. Consulting, correcting and re-recording are the
    same three gestures as for a file, because it is the same object.

    Two limits are deliberate:
      * **A row cap.** A session lives in memory, so a very large table has to be
        refused rather than quietly taking the process down.
      * **Encrypted cells are masked.** A confidential column stored by another
        session would come back as ciphertext; showing `enc:v1:…` to someone
        would be worse than useless, and decrypting it here would bypass the
        holder list entirely.
    """
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))

    scope = env or d.environment or repo.DEFAULT_ENV
    perm = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
    if not repo.can_on_dataset(perm, "read"):
        raise HTTPException(403, f"Aucun accès en lecture à la table « {d.name} ».")

    total = repo.count_rows(s, dataset_id)
    cap = max(1, min(int(limit), 200_000))
    if total > cap:
        raise HTTPException(
            413, f"La table « {d.name} » contient {total} lignes, au-delà de la "
                 f"limite de {cap}. Utilisez un flux avec une brique `dataset` "
                 f"pour la traiter par lots.")

    rows = [r.data for r in repo.read_rows(s, dataset_id, offset=0, limit=cap)]
    cols = list((d.schema_json or {}).get("columns", []))
    df = pd.DataFrame(rows)
    if cols:
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
    df = df.astype("string").fillna("")
    df, masked = ds.mask_encrypted_columns(df)

    sid = store.create(df, file_type="TABLE", encoding="N/A", delimiter="N/A")
    sess = store.get(sid)
    if masked:
        # Keep the mark so a later write cannot silently store the mask as data.
        sess.sensitivity = {c: "?" for c in masked}
    return FileResponse(session_id=sid, type="TABLE", encoding="N/A",
                        delimiter="N/A", preview=_table_preview(df))


# ══════════════════════════════════════════════════════════════════════
# Sharing a table
# ══════════════════════════════════════════════════════════════════════
class GrantIn(BaseModel):
    email: str = ""                       # grant to one person…
    role: str = ""                        # …or to everyone holding a role here…
    environment: str = ""                 # …or to a whole other environment (read only)
    permission: str = "read"              # read | write | manage


@router.get("/datasets/{dataset_id}/grants")
def list_dataset_grants(dataset_id: str, env: str = "", user=Depends(require_user),
                        s: Session = Depends(get_session)):
    """Who may do what on this table, and what the caller may do."""
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    scope = env or d.environment or repo.DEFAULT_ENV
    mine = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
    if not repo.can_on_dataset(mine, "read"):
        raise HTTPException(403, f"Aucun accès à la table « {d.name} ».")
    out = []
    for g in repo.list_grants(s, dataset_id):
        label = g.subject
        if g.subject_kind == "user":
            u = s.get(User, g.subject)
            label = u.email if u else g.subject
        out.append({"subject_kind": g.subject_kind, "subject": g.subject,
                    "label": label, "permission": g.permission})
    return {"owner_id": d.owner_id, "is_managed": d.is_managed,
            "my_permission": mine, "grants": out}


@router.post("/datasets/{dataset_id}/grants")
def set_dataset_grant(dataset_id: str, req: GrantIn, env: str = "",
                      user=Depends(require_user), s: Session = Depends(get_session)):
    """
    Share a table. Only someone who *manages* it may — that is the owner, an
    environment admin, or somebody explicitly given manage. Sharing is itself a
    permission, otherwise anyone with write could widen access indefinitely.
    """
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    scope = env or d.environment or repo.DEFAULT_ENV
    mine = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
    if not repo.can_on_dataset(mine, "manage"):
        raise HTTPException(403, f"Seul un gestionnaire de « {d.name} » peut la partager.")

    if req.email:
        target = _auth.find_user(s, req.email)
        if target is None:
            raise HTTPException(404, f"Aucun compte pour {req.email}.")
        subject, kind = target.id, "user"
    elif req.role:
        if req.role not in _auth.ROLES:
            raise HTTPException(422, f"Rôle inconnu '{req.role}'.")
        subject, kind = req.role, "role"
    elif req.environment:
        subject, kind = req.environment, "environment"
    else:
        raise HTTPException(422, "Indiquez un email, un rôle ou un environnement.")

    try:
        repo.grant_on_dataset(s, dataset_id, subject=subject, subject_kind=kind,
                              permission=req.permission,
                              granted_by=getattr(user, "id", ""))
    except repo.Conflict as e:
        raise HTTPException(422, str(e))
    commit(s)
    return list_dataset_grants(dataset_id, env, user, s)


@router.delete("/datasets/{dataset_id}/grants/{subject}")
def remove_dataset_grant(dataset_id: str, subject: str, kind: str = "user",
                         env: str = "", user=Depends(require_user),
                         s: Session = Depends(get_session)):
    try:
        d = repo.get_dataset(s, dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    scope = env or d.environment or repo.DEFAULT_ENV
    mine = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
    if not repo.can_on_dataset(mine, "manage"):
        raise HTTPException(403, f"Seul un gestionnaire de « {d.name} » peut la partager.")
    repo.revoke_on_dataset(s, dataset_id, subject, kind)
    commit(s)
    return {"revoked": subject}
