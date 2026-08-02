"""
store_routes.py
───────────────
HTTP surface for the persistence layer. Mounted by main.py.

Endpoints (all under /api):

  Artefacts (config | computed | tco)
    POST   /artefacts/{kind}                 create (v1)
    GET    /artefacts/{kind}                  list
    GET    /artefacts/{kind}/{id}             detail + version list
    POST   /artefacts/{kind}/{id}/versions    append a version ("edit")
    GET    /artefacts/{kind}/{id}/versions/{no}   fetch one version's body
    DELETE /artefacts/{kind}/{id}             archive

  Flows
    POST   /flows                             create (compose config+tco+computed)
    GET    /flows                             list
    GET    /flows/{id}                        detail
    PATCH  /flows/{id}                         update refs
    DELETE /flows/{id}                        archive
    POST   /flows/{id}/run                    run on an uploaded file  ← the point

  Runs
    GET    /runs                              list (optionally by flow)
    GET    /runs/{id}                         detail (report + summary)
    GET    /runs/{id}/export                  download the produced CSV/XLSX
"""

from __future__ import annotations

import base64
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.auth_routes import current_env, require_capability, require_user
from app.db_models import KINDS
from app.models import (
    PipelineErrorCell, PipelineErrorRow, PipelineResponse, PipelineStructure, ProcessStats,
)
from app.services import store_service as store
from app.store_models import (
    ArtefactCreate, ArtefactDetail, ArtefactInfo, ArtefactUpdate, FlowCreate,
    FlowInfo, FlowUpdate, RunDetail, RunInfo, VersionBody, VersionInfo,
)

router = APIRouter(prefix="/api")


def _kind_or_404(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, f"Unknown kind '{kind}'. Use one of {', '.join(KINDS)}.")
    return kind


# Which capability writing each kind of artefact needs. A kind absent from the
# map falls back to `config.write`: a new kind must be *granted* explicitly, not
# writable by default.
KIND_CAPABILITY = {
    "config": "config.write",
    "computed": "config.write",
    "tco": "tco.replace",
    "edi_model": "edi_model.write",
    "mapping": "mapping.write",
    "graph": "flow.write",
    "function": "function.write",
}


def _check_kind_capability(s: Session, user, kind: str, environment: str) -> None:
    _check_capability_in(s, user, environment, KIND_CAPABILITY.get(kind, "config.write"))


def _check_capability_in(s: Session, user, environment: str, capability: str) -> None:
    """Check the caller's role in `environment` for an explicit capability —
    used where the capability isn't the kind's write one (e.g. archiving is
    `config.delete`, a stricter, admin-only bar, not `config.write`)."""
    from app.services import auth_service as _auth
    from app.services import permissions as _perms
    if not getattr(user, "id", ""):
        return                                  # setup mode
    scope = environment or repo.DEFAULT_ENV
    role = _auth.role_in(s, user, scope)
    if not _perms.can(role, capability):
        spec = _perms.CAPABILITIES.get(capability, {})
        raise HTTPException(403, f"« {spec.get('label', capability)} » demande le rôle "
                                 f"'{spec.get('min','?')}' dans '{scope}' "
                                 f"(vous êtes '{role or 'non-membre'}').")


def _artefact_info(a) -> ArtefactInfo:
    return ArtefactInfo(
        id=a.id, kind=a.kind, name=a.name, description=a.description,
        environment=getattr(a, "environment", "default") or "default",
        derived_from=getattr(a, "derived_from", "") or "",
        derived_from_version=getattr(a, "derived_from_version", None),
        latest_version_no=a.latest_version_no, archived=a.archived,
        created_at=a.created_at, updated_at=a.updated_at)


# ══════════════════════════════════════════════════════════════════
# ARTEFACTS
# ══════════════════════════════════════════════════════════════════
@router.post("/artefacts/{kind}", response_model=ArtefactDetail, status_code=201)
def create_artefact(kind: str, req: ArtefactCreate, env: str = "",
                    user=Depends(require_user),
                    s: Session = Depends(get_session)):
    _kind_or_404(kind)
    # The capability follows the kind: writing a config is a design act, while
    # a correspondence table is part of doing the work. Same route, different
    # bar — which is precisely the distinction an HR team needs.
    _check_kind_capability(s, user, kind, env or req.environment)
    if req.environment and getattr(user, "id", ""):
        _check_kind_capability(s, user, kind, req.environment)
    try:
        body = store.normalise_body(kind, body=req.body, yaml=req.yaml,
                                    computed=req.computed, csv=req.csv,
                                    sql_computed=req.sql_computed,
                                    style_rules=req.style_rules)
    except store.BadBody as e:
        raise HTTPException(422, str(e))
    try:
        # A derived artefact must descend from the same kind: deriving a config
        # from a mapping would produce something nobody can interpret.
        if req.derived_from:
            try:
                parent = repo.get_artefact(s, req.derived_from)
            except repo.NotFound as e:
                raise HTTPException(404, f"Artefact d'origine introuvable : {e}")
            if parent.kind != kind:
                raise HTTPException(
                    409, f"« {parent.name} » est un {parent.kind} : on ne peut pas "
                         f"en dériver un {kind}.")
        ver = repo.create_artefact(s, kind, req.name, body, req.description, req.note,
                                   environment=req.environment,
                                   derived_from=req.derived_from,
                                   derived_from_version=req.derived_from_version)
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return _detail(s, ver.artefact_id)


@router.get("/artefacts/{kind}", response_model=list[ArtefactInfo])
def list_artefacts(kind: str, include_archived: bool = False,
                   scope: str = Depends(current_env),
                   s: Session = Depends(get_session)):
    """
    Artefacts of one environment.

    `scope` is resolved by the guard from the caller's session, not taken from
    the query string: asking for an environment one does not belong to is
    refused rather than silently answered with the default. That is what turns
    `?env=` from a display convention into a boundary.
    """
    _kind_or_404(kind)
    return [_artefact_info(a) for a in repo.list_visible_artefacts(s, kind, include_archived,
                                                                   environment=scope)]


@router.get("/artefacts/{kind}/{artefact_id}/lineage")
def artefact_lineage(kind: str, artefact_id: str,
                     scope: str = Depends(current_env),
                     s: Session = Depends(get_session)):
    """
    The family of an artefact: what it descends from, what descends from it.

    Useful the day the library holds thirty configurations and someone asks
    which of them is the real contract.
    """
    _kind_or_404(kind)
    try:
        return repo.lineage(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))


@router.get("/environments")
def list_environments(s: Session = Depends(get_session)):
    """Every environment holding something, so the UI can offer them."""
    return {"environments": repo.list_environments(s), "default": repo.DEFAULT_ENV}


def _user_can_view_artefact(s: Session, user, art) -> bool:
    """
    Whether this specific person may read this artefact, through any
    environment they belong to — not just the one named in the URL, since
    these routes are reached by id and the caller rarely states a scope.

    Visible through ownership, an explicit grant, or an EnvironmentProfile pin
    (`repo.artefact_visible`, checked once per environment the user is a
    member of) — a "locked configuration" environment could otherwise never
    actually read the configuration it is locked to.
    """
    if not getattr(user, "id", ""):
        return True                             # setup mode: no accounts yet
    if getattr(user, "is_superadmin", False):
        return True
    from app.services import auth_service as _auth
    envs = set(_auth.memberships_of(s, user.id).keys())
    return any(repo.artefact_visible(s, art, e) for e in envs)


@router.get("/artefacts/{kind}/{artefact_id}", response_model=ArtefactDetail)
def get_artefact(kind: str, artefact_id: str, user=Depends(require_user),
                 s: Session = Depends(get_session)):
    _kind_or_404(kind)
    return _detail(s, artefact_id, kind, user)


def _detail(s: Session, artefact_id: str, kind: str | None = None, user=None) -> ArtefactDetail:
    try:
        a = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if kind and a.kind != kind:
        raise HTTPException(404, f"{artefact_id} is a '{a.kind}', not a '{kind}'.")
    if user is not None and not _user_can_view_artefact(s, user, a):
        # 404, not 403: an artefact one cannot see must not even prove it exists.
        raise HTTPException(404, f"Artefact {artefact_id} not found.")
    return ArtefactDetail(
        **_artefact_info(a).model_dump(),
        versions=[VersionInfo(id=v.id, version_no=v.version_no, note=v.note,
                              created_at=v.created_at) for v in a.versions])


# ══════════════════════════════════════════════════════════════════
# ARTEFACT GRANTS — sharing an artefact with another environment
# ══════════════════════════════════════════════════════════════════
class ArtefactGrantIn(BaseModel):
    environment: str


def _require_owner_admin(s: Session, user, art) -> None:
    """
    Only an admin of the *owning* environment may grant access to its own
    artefact — the beneficiary environment has no say in what it receives.
    """
    from app.services import auth_service as _auth
    if not getattr(user, "id", ""):
        return                                  # setup mode
    role = _auth.role_in(s, user, art.environment)
    if role != "admin":
        raise HTTPException(
            403, f"Seul un administrateur de « {art.environment} » peut partager "
                 f"cet artefact.")


@router.get("/artefacts/{kind}/{artefact_id}/grants")
def list_artefact_grants(kind: str, artefact_id: str,
                         user=Depends(require_user), s: Session = Depends(get_session)):
    _kind_or_404(kind)
    try:
        a = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    _require_owner_admin(s, user, a)
    return {"owner_environment": a.environment,
            "grants": [{"environment": g.subject, "permission": g.permission}
                      for g in repo.list_artefact_grants(s, artefact_id)]}


@router.post("/artefacts/{kind}/{artefact_id}/grants")
def set_artefact_grant(kind: str, artefact_id: str, req: ArtefactGrantIn,
                       user=Depends(require_user), s: Session = Depends(get_session)):
    _kind_or_404(kind)
    try:
        a = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    _require_owner_admin(s, user, a)
    if not req.environment.strip():
        raise HTTPException(422, "Indiquez un environnement.")
    if req.environment == a.environment:
        raise HTTPException(422, "Cet environnement possède déjà l'artefact.")
    repo.grant_artefact_to_environment(s, artefact_id, environment=req.environment,
                                       granted_by=getattr(user, "id", ""))
    commit(s)
    return list_artefact_grants(kind, artefact_id, user, s)


@router.delete("/artefacts/{kind}/{artefact_id}/grants/{environment}")
def remove_artefact_grant(kind: str, artefact_id: str, environment: str,
                          user=Depends(require_user), s: Session = Depends(get_session)):
    _kind_or_404(kind)
    try:
        a = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    _require_owner_admin(s, user, a)
    repo.revoke_artefact_from_environment(s, artefact_id, environment)
    commit(s)
    return {"revoked": environment}


@router.post("/artefacts/{kind}/{artefact_id}/versions", response_model=ArtefactDetail)
def add_version(kind: str, artefact_id: str, req: ArtefactUpdate, env: str = "",
                user=Depends(require_user), s: Session = Depends(get_session)):
    _kind_or_404(kind)
    try:
        a = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if a.kind != kind:
        raise HTTPException(404, f"{artefact_id} is a '{a.kind}', not a '{kind}'.")
    # Adding a version *is* modifying: guarding creation alone would leave the
    # obvious way round it open — an operator could rewrite a configuration by
    # appending to it instead of creating one.
    _check_kind_capability(s, user, kind, env or getattr(a, "environment", ""))
    try:
        body = store.normalise_body(kind, body=req.body, yaml=req.yaml,
                                    computed=req.computed, csv=req.csv,
                                    sql_computed=req.sql_computed,
                                    style_rules=req.style_rules)
    except store.BadBody as e:
        raise HTTPException(422, str(e))
    repo.add_version(s, artefact_id, body, req.note)
    commit(s)
    return _detail(s, artefact_id)


@router.get("/artefacts/{kind}/{artefact_id}/versions/{version_no}", response_model=VersionBody)
def get_version(kind: str, artefact_id: str, version_no: int,
                user=Depends(require_user), s: Session = Depends(get_session)):
    _kind_or_404(kind)
    try:
        ver = repo.resolve_ref(s, artefact_id, version_no)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if not _user_can_view_artefact(s, user, ver.artefact):
        raise HTTPException(404, f"Artefact {artefact_id} not found.")
    return VersionBody(id=ver.id, artefact_id=artefact_id, kind=kind,
                       version_no=ver.version_no, body=ver.body, note=ver.note,
                       created_at=ver.created_at)


@router.get("/artefacts/config/{artefact_id}/versions/{version_no}/yaml")
def get_config_version_yaml(artefact_id: str, version_no: int,
                            user=Depends(require_user), s: Session = Depends(get_session)):
    """A stored config version rendered back to YAML — for the UI 'load from
    library' path and for humans who want to read or diff a version."""
    try:
        ver = repo.resolve_ref(s, artefact_id, version_no)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if not _user_can_view_artefact(s, user, ver.artefact):
        raise HTTPException(404, f"Artefact {artefact_id} not found.")
    if ver.artefact.kind != "config":
        raise HTTPException(404, f"{artefact_id} is a '{ver.artefact.kind}', not a config.")
    from app.models import FileConfig
    from app.services.config_service import ConfigService
    return {"yaml": ConfigService().to_yaml(FileConfig(**ver.body)), "version_no": ver.version_no}


@router.delete("/artefacts/{kind}/{artefact_id}")
def archive_artefact(kind: str, artefact_id: str, s: Session = Depends(get_session),
        user=Depends(require_user)):
    _kind_or_404(kind)
    try:
        art = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    # Archiving reaches into whatever environment actually *owns* this
    # artefact — never the caller's query-string `env`, which a "default"
    # admin could otherwise use to delete another environment's artefacts
    # just by knowing their id (same reasoning as add_version above).
    # `config.delete` specifically, not the kind's write capability — an
    # editor may design a configuration but archiving shared material is a
    # stricter, admin-only act.
    _check_capability_in(s, user, art.environment, "config.delete")
    try:
        repo.archive_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return {"archived": artefact_id}


@router.post("/artefacts/{kind}/{artefact_id}/restore")
def restore_artefact(kind: str, artefact_id: str, s: Session = Depends(get_session),
        user=Depends(require_user)):
    _kind_or_404(kind)
    try:
        art = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    _check_capability_in(s, user, art.environment, "config.delete")
    repo.restore_artefact(s, artefact_id)
    commit(s)
    return {"restored": artefact_id}


@router.delete("/artefacts/{kind}/{artefact_id}/permanent")
def delete_artefact_permanently(kind: str, artefact_id: str, s: Session = Depends(get_session),
        user=Depends(require_user)):
    """Gone for good — every version, no history. Reserved for something
    already archived; refused while a live flow or a profile still needs it."""
    _kind_or_404(kind)
    try:
        art = repo.get_artefact(s, artefact_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    _check_capability_in(s, user, art.environment, "config.delete")
    try:
        repo.delete_artefact_permanently(s, artefact_id)
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return {"deleted": artefact_id}


# ══════════════════════════════════════════════════════════════════
# FLOWS
# ══════════════════════════════════════════════════════════════════
def _flow_info(f) -> FlowInfo:
    return FlowInfo(
        id=f.id, name=f.name, description=f.description, archived=f.archived,
        config_artefact_id=f.config_artefact_id, config_version_no=f.config_version_no,
        tco_artefact_id=f.tco_artefact_id, tco_version_no=f.tco_version_no,
        computed_artefact_id=f.computed_artefact_id, computed_version_no=f.computed_version_no,
        default_export_filename=f.default_export_filename)


@router.post("/flows", response_model=FlowInfo, status_code=201)
def create_flow(req: FlowCreate, s: Session = Depends(get_session)):
    try:
        flow = repo.create_flow(s, **req.model_dump())
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    except repo.NotFound as e:
        raise HTTPException(422, str(e))
    commit(s)
    return _flow_info(flow)


@router.get("/flows", response_model=list[FlowInfo])
def list_flows(include_archived: bool = False, s: Session = Depends(get_session)):
    return [_flow_info(f) for f in repo.list_flows(s, include_archived)]


@router.get("/flows/{flow_id}", response_model=FlowInfo)
def get_flow(flow_id: str, s: Session = Depends(get_session)):
    try:
        return _flow_info(repo.get_flow(s, flow_id))
    except repo.NotFound as e:
        raise HTTPException(404, str(e))


@router.patch("/flows/{flow_id}", response_model=FlowInfo)
def update_flow(flow_id: str, req: FlowUpdate, s: Session = Depends(get_session)):
    try:
        flow = repo.update_flow(s, flow_id, **req.model_dump(exclude_none=True))
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return _flow_info(flow)


@router.delete("/flows/{flow_id}")
def archive_flow(flow_id: str, s: Session = Depends(get_session)):
    try:
        repo.archive_flow(s, flow_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return {"archived": flow_id}


@router.post("/flows/{flow_id}/restore")
def restore_flow(flow_id: str, s: Session = Depends(get_session)):
    try:
        repo.restore_flow(s, flow_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return {"restored": flow_id}


@router.delete("/flows/{flow_id}/permanent")
def delete_flow_permanently(flow_id: str, s: Session = Depends(get_session)):
    try:
        repo.delete_flow_permanently(s, flow_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return {"deleted": flow_id}


@router.post("/flows/{flow_id}/run", response_model=PipelineResponse)
async def run_flow(flow_id: str, file: UploadFile = File(...),
                   export_filename: str = Form(""), s: Session = Depends(get_session)):
    """Run a stored flow on an uploaded file. Persists a Run and returns the
    same PipelineResponse shape as /api/pipeline — plus the run id in warnings-free
    form via the `run_id` field the client can read from the response headers."""
    from app.main import _apply_filters   # reuse the AND/OR filter logic

    try:
        flow = repo.get_flow(s, flow_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")

    try:
        run, res = store.run_flow(s, flow, raw, file.filename or "",
                                  apply_filters=_apply_filters,
                                  export_filename=export_filename or None)
    except repo.NotFound as e:
        raise HTTPException(422, f"Flow inputs unresolved: {e}")

    resp = _engine_to_response(res)
    commit(s)                          # the Run must exist before the client hears about it
    return _with_run_header(resp, run.id)


# ══════════════════════════════════════════════════════════════════
# RUNS
# ══════════════════════════════════════════════════════════════════
def _run_info(r) -> RunInfo:
    return RunInfo(
        id=r.id, flow_id=r.flow_id, flow_name=r.flow_name, source_name=r.source_name,
        ok=r.ok, stage=r.stage, error=r.error, rows_total=r.rows_total,
        rows_error=r.rows_error, rows_cleaned=r.rows_cleaned, created_at=r.created_at)


@router.get("/runs", response_model=list[RunInfo])
def list_runs(flow_id: str | None = None, limit: int = 50, s: Session = Depends(get_session)):
    return [_run_info(r) for r in repo.list_runs(s, flow_id, limit)]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, s: Session = Depends(get_session)):
    try:
        r = repo.get_run(s, run_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    return RunDetail(
        **_run_info(r).model_dump(),
        config_version_id=r.config_version_id, tco_version_id=r.tco_version_id,
        computed_version_id=r.computed_version_id, summary=r.summary or {},
        report=r.report or {}, has_export=bool(r.export_b64),
        export_name=r.export_name, export_format=r.export_format)


@router.get("/runs/{run_id}/export")
def download_run_export(run_id: str, s: Session = Depends(get_session)):
    try:
        r = repo.get_run(s, run_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    if not r.export_b64:
        raise HTTPException(404, "This run produced no export (it had blocking errors).")
    data = base64.b64decode(r.export_b64)
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if r.export_format == "xlsx" else "text/csv")
    return StreamingResponse(io.BytesIO(data), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{r.export_name or "export"}"'})


# ── engine result → PipelineResponse (same shape as /api/pipeline) ────
def _engine_to_response(res) -> PipelineResponse:
    structure = PipelineStructure(**res.structure) if res.structure else None
    stats = ProcessStats(**res.stats) if res.stats else None
    report = [PipelineErrorRow(id=row["id"],
              errors=[PipelineErrorCell(**c) for c in row["errors"]]) for row in res.report]
    export = None
    if res.export:
        from app.models import PipelineExport
        export = PipelineExport(**res.export)
    return PipelineResponse(
        ok=res.ok, stage=res.stage, error=res.error, structure=structure,
        matched_columns=res.matched_columns, missing_columns=res.missing_columns,
        extra_columns=res.extra_columns, identifier_field=res.identifier_field,
        stats=stats, compute_errors=res.compute_errors, tco_uncovered=res.tco_uncovered,
        report=report, warnings=res.warnings, export=export)


def _with_run_header(resp: PipelineResponse, run_id: str):
    from fastapi.responses import JSONResponse
    payload = resp.model_dump()
    payload["run_id"] = run_id            # body + header: clients read whichever is easier
    return JSONResponse(payload, headers={"X-Run-Id": run_id})
