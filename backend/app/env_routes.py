"""
Environment profiles — deploying the same application to different audiences.

Scoping the library decided what an environment *owns*. This decides what it
*exposes*: which modules appear, which configuration is imposed, what may still
be edited, and which buttons are offered.

The templates below are the "few clicks" part. `controle_simple` is the one an
HR or ADV team gets: they open the app, drop their file, see it checked against
a configuration they cannot alter, read the report, and — when the failure is a
mapping one — extend the correspondence table without touching anything else.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.auth_routes import require_capability, require_user
from app.db_models import Artefact, CryptoKey, Dataset, EnvironmentProfile
from app.services.tco_service import TcoService

router = APIRouter(prefix="/api/environments", tags=["environments"])


def _check_env_capability(s: Session, user, environment: str, capability: str = "env.profile") -> None:
    """
    Check the caller's role in `environment` itself — never in a separately
    supplied `env` query param. `require_capability`'s own `env` parameter
    defaults to "default" when absent, so a route reached by a path segment
    like `/{name}/profile` must check the role against `name`, not against
    whatever (or nothing) a caller happened to pass as `?env=` — otherwise an
    admin of "default" could edit or read another environment's profile
    just by naming it in the path.
    """
    from app.services import auth_service as _auth
    from app.services import permissions as _perms
    if not getattr(user, "id", ""):
        return                                  # setup mode
    role = _auth.role_in(s, user, environment)
    if not _perms.can(role, capability):
        spec = _perms.CAPABILITIES.get(capability, {})
        raise HTTPException(403, f"« {spec.get('label', capability)} » demande le rôle "
                                 f"'{spec.get('min', '?')}' dans '{environment}' "
                                 f"(vous êtes '{role or 'non-membre'}').")


# Every module the application can show. A profile names a subset.
ALL_MODULES = ["schema", "computed", "data", "report", "yaml", "flows", "edi",
               "datasets", "mapping", "canvas", "functions", "ops", "tco"]

TEMPLATES: Dict[str, dict] = {
    "complet": {
        "label": "Complet (tous les modules)",
        "description": "Tous les modules, configuration libre — le profil de l'équipe data.",
        "modules": list(ALL_MODULES),
        "config_locked": False,
        "tco_editable": True,
    },
    "controle_simple": {
        "label": "Contrôle de fichier",
        "description": ("Déposer un fichier, le contrôler contre une configuration imposée, "
                        "lire le rapport, corriger la table de correspondance."),
        # Deliberately short: no schema editor, no flows, no canvas. What is not
        # shown cannot be broken, and an operator asked to ignore nine tabs will
        # eventually click one of them.
        "modules": ["data", "report", "tco"],
        "config_locked": True,
        "tco_editable": True,
    },
    "consultation": {
        "label": "Consultation",
        "description": "Lecture seule : contrôler et lire le rapport, sans rien modifier.",
        "modules": ["data", "report"],
        "config_locked": True,
        "tco_editable": False,
    },
}


class ProfileIn(BaseModel):
    label: str = ""
    description: str = ""
    modules: Optional[List[str]] = None
    config_artefact_id: str = ""
    config_version_no: Optional[int] = None
    config_locked: Optional[bool] = None
    tco_artefact_id: str = ""
    tco_editable: Optional[bool] = None
    actions: Optional[List[Dict[str, Any]]] = None
    max_open_tabs: Optional[int] = None


class CreateEnvIn(BaseModel):
    name: str
    template: str = "complet"
    label: str = ""
    config_artefact_id: str = ""
    tco_artefact_id: str = ""


def _out(p: EnvironmentProfile) -> dict:
    return {
        "name": p.name, "label": p.label or p.name, "description": p.description,
        # An empty list means "everything": an environment created before
        # profiles existed must keep working untouched.
        "modules": list(p.modules_json or []) or list(ALL_MODULES),
        "config_artefact_id": p.config_artefact_id,
        "config_version_no": p.config_version_no,
        "config_locked": p.config_locked,
        "tco_artefact_id": p.tco_artefact_id, "tco_editable": p.tco_editable,
        "actions": list(p.actions_json or []),
        "max_open_tabs": p.max_open_tabs,
    }


def _default_profile(name: str) -> dict:
    return {"name": name, "label": name, "description": "", "modules": list(ALL_MODULES),
            "config_artefact_id": "", "config_version_no": None, "config_locked": False,
            "tco_artefact_id": "", "tco_editable": True, "actions": [], "max_open_tabs": 0}


@router.get("/templates")
def list_templates():
    """The starting points offered when creating an environment."""
    return {"templates": [{"key": k, **{kk: vv for kk, vv in v.items()}}
                          for k, v in TEMPLATES.items()],
            "modules": ALL_MODULES}


@router.get("/{name}/profile")
def get_profile(name: str, s: Session = Depends(get_session)):
    p = s.get(EnvironmentProfile, name)
    return _out(p) if p is not None else _default_profile(name)


@router.post("/{name}/profile")
def set_profile(name: str, req: ProfileIn, s: Session = Depends(get_session),
        user=Depends(require_user)):
    _check_env_capability(s, user, name)
    p = s.get(EnvironmentProfile, name)
    if p is None:
        p = EnvironmentProfile(name=name, modules_json=[], actions_json=[])
        s.add(p)
    if req.label:
        p.label = req.label
    if req.description:
        p.description = req.description
    if req.modules is not None:
        unknown = [m for m in req.modules if m not in ALL_MODULES]
        if unknown:
            raise HTTPException(422, f"Unknown module(s): {', '.join(unknown)}")
        p.modules_json = list(req.modules)
    if req.config_artefact_id:
        p.config_artefact_id = req.config_artefact_id
        p.config_version_no = req.config_version_no
    if req.config_locked is not None:
        p.config_locked = req.config_locked
    if req.tco_artefact_id:
        p.tco_artefact_id = req.tco_artefact_id
    if req.tco_editable is not None:
        p.tco_editable = req.tco_editable
    if req.actions is not None:
        p.actions_json = list(req.actions)
    if req.max_open_tabs is not None:
        if req.max_open_tabs < 0:
            raise HTTPException(422, "max_open_tabs ne peut pas être négatif.")
        p.max_open_tabs = req.max_open_tabs
    # A locked configuration that names no configuration would lock users out of
    # a screen they cannot use — refuse it rather than ship a dead end.
    if p.config_locked and not p.config_artefact_id:
        raise HTTPException(422, "A locked environment must pin a configuration.")
    commit(s)
    return _out(p)


@router.post("")
def create_environment(req: CreateEnvIn, s: Session = Depends(get_session),
        _cap=Depends(require_capability("env.profile"))):
    """Create an environment from a template — the 'few clicks' path."""
    name = (req.name or "").strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(422, "An environment needs a name.")
    if s.get(EnvironmentProfile, name) is not None:
        raise HTTPException(409, f"Environment '{name}' already exists.")
    tpl = TEMPLATES.get(req.template)
    if tpl is None:
        raise HTTPException(422, f"Unknown template '{req.template}'.")
    if tpl["config_locked"] and not req.config_artefact_id:
        raise HTTPException(
            422, f"The '{req.template}' template imposes a configuration: "
                 f"pick the config it should pin (config_artefact_id).")

    p = EnvironmentProfile(
        name=name, label=req.label or tpl["label"], description=tpl["description"],
        modules_json=list(tpl["modules"]), config_artefact_id=req.config_artefact_id,
        config_locked=tpl["config_locked"], tco_artefact_id=req.tco_artefact_id,
        tco_editable=tpl["tco_editable"], actions_json=[])
    s.add(p)
    commit(s)
    return _out(p)


@router.delete("/{name}/profile")
def reset_profile(name: str, s: Session = Depends(get_session),
        user=Depends(require_user)):
    """Drop the profile: the environment reverts to showing everything. The
    artefacts it owns are untouched — a profile describes exposure, not data."""
    _check_env_capability(s, user, name)
    p = s.get(EnvironmentProfile, name)
    if p is not None:
        s.delete(p)
        commit(s)
    return {"reset": name}


@router.get("/{name}/content")
def environment_content(name: str, include_archived: bool = False,
        s: Session = Depends(get_session), user=Depends(require_user)):
    """
    What this environment owns, by name.

    Two callers, two needs: the "delete this environment" migration
    checklist wants only what's actually live (archived material isn't
    worth migrating), while a "manage the library" screen wants everything,
    archived included, since that's precisely what it lets an admin act on.
    `include_archived` tells them apart — omitting it used to mean archived
    configs and graphs sat in these lists looking exactly as live as
    everything else, with no badge and no way to tell.
    """
    _check_env_capability(s, user, name)
    art_q = select(Artefact).where(Artefact.environment == name)
    if not include_archived:
        art_q = art_q.where(Artefact.archived == False)  # noqa: E712
    artefacts = [{"id": a.id, "kind": a.kind, "name": a.name, "archived": a.archived}
                for a in s.scalars(art_q)]
    ds_q = select(Dataset).where(Dataset.environment == name)
    if not include_archived:
        ds_q = ds_q.where(Dataset.archived == False)  # noqa: E712
    datasets = [{"id": d.id, "name": d.name, "archived": d.archived}
               for d in s.scalars(ds_q)]
    keys = [{"id": k.id, "name": k.name, "active": k.active}
           for k in s.scalars(select(CryptoKey).where(CryptoKey.environment == name))]
    return {"artefacts": artefacts, "datasets": datasets, "keys": keys}


class DeleteEnvIn(BaseModel):
    migrate_artefact_ids: List[str] = Field(default_factory=list)
    migrate_dataset_ids: List[str] = Field(default_factory=list)
    migrate_key_ids: List[str] = Field(default_factory=list)
    target_environment: str = "default"
    mode: str = "profile_only"      # "profile_only" | "cascade"
    confirm_name: str = ""          # required, and must match `name`, in cascade mode


@router.delete("/{name}")
def delete_environment(name: str, req: DeleteEnvIn,
                       user=Depends(require_user), s: Session = Depends(get_session)):
    """
    Remove an environment. Chosen artefacts/datasets/keys are migrated to
    `target_environment` first; whatever is left is either just detached
    (`profile_only` — the environment becomes orphaned, its data untouched)
    or hard-deleted (`cascade`).

    This crosses environments — it writes into another one and can destroy
    data no single environment's admin owns alone — so it needs a superadmin
    rather than the `env.profile` capability used everywhere else in this file.
    """
    if getattr(user, "id", "") and not user.is_superadmin:
        raise HTTPException(403, "Seul un administrateur général peut supprimer un environnement.")
    if name == repo.DEFAULT_ENV:
        raise HTTPException(422, "L'environnement « default » ne peut pas être supprimé.")
    if req.mode not in ("profile_only", "cascade"):
        raise HTTPException(422, "mode doit être 'profile_only' ou 'cascade'.")
    if req.mode == "cascade" and req.confirm_name != name:
        raise HTTPException(
            422, "Tapez le nom de l'environnement pour confirmer la suppression en cascade.")

    try:
        for aid in req.migrate_artefact_ids:
            repo.move_artefact_to_environment(s, aid, name, req.target_environment)
        for did in req.migrate_dataset_ids:
            repo.move_dataset_to_environment(s, did, name, req.target_environment)
        for kid in req.migrate_key_ids:
            repo.move_crypto_key_to_environment(s, kid, name, req.target_environment)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    except repo.Conflict as e:
        raise HTTPException(409, str(e))

    try:
        if req.mode == "cascade":
            repo.delete_environment_cascade(s, name)
        else:
            repo.delete_environment_profile_only(s, name)
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return {"deleted": name, "mode": req.mode}


# ══════════════════════════════════════════════════════════════════════
# Correspondence table: extend it from what the run could not map
# ══════════════════════════════════════════════════════════════════════
class TcoSuggestIn(BaseModel):
    uncovered: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    field_types: Dict[str, str] = Field(default_factory=dict)


@router.post("/tco/suggest")
def suggest_tco(req: TcoSuggestIn):
    """
    Turn "these values were not mapped" into rows ready to be completed.

    The type comes from the field's configuration, not from the operator: it is
    already declared there, and asking twice is how two sources of truth start
    disagreeing. Only the target label is left to fill in — which is the one
    thing the machine genuinely cannot infer.
    """
    rows = []
    for column, values in (req.uncovered or {}).items():
        for entry in values:
            rows.append({
                "TYPE": req.field_types.get(column, column),
                "SOURCE_VALUE": str(entry.get("value", "")),
                "TARGET_LABEL": "",
                "count": int(entry.get("count", 0) or 0),
                "column": column,
            })
    rows.sort(key=lambda r: -r["count"])
    return {"rows": rows, "total": len(rows)}


class TcoTargetSourceIn(BaseModel):
    dataset_id: str
    query: str


@router.post("/tco/resolve-target-values")
def resolve_tco_target_values(req: TcoTargetSourceIn, s: Session = Depends(get_session),
        user=Depends(require_user)):
    """
    The allowed-values list a `target_source` currently resolves to — used
    both to preview one while configuring it, and to populate the dropdown
    that replaces free-text entry once one is set.
    """
    from app.services import auth_service as _auth
    from app.services import tco_target_service

    try:
        d = repo.get_dataset(s, req.dataset_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    scope = d.environment or repo.DEFAULT_ENV
    perm = repo.dataset_permission(s, d, user, _auth.role_in(s, user, scope), environment=scope)
    if not repo.can_on_dataset(perm, "read"):
        raise HTTPException(403, f"Aucun accès en lecture à la table « {d.name} ».")
    try:
        values = tco_target_service.resolve_allowed_values(s, req.dataset_id, req.query)
    except tco_target_service.BadTargetSource as e:
        raise HTTPException(422, str(e))
    return {"values": values}


class TcoAppendIn(BaseModel):
    artefact_id: str = ""
    name: str = ""
    environment: str = ""
    rows: List[Dict[str, str]] = Field(default_factory=list)


@router.post("/tco/append")
def append_tco(req: TcoAppendIn, s: Session = Depends(get_session),
        user=Depends(require_user)):
    """
    Add rows to a correspondence table — as a new version, never in place.

    A TCO is an artefact, so extending it appends a version: the run that used
    the previous one remains reproducible. Rows with an empty target are refused,
    since a mapping to nothing is the very error being fixed.
    """
    rows = [r for r in req.rows if str(r.get("SOURCE_VALUE", "")).strip()]
    if not rows:
        raise HTTPException(422, "No row to add.")
    empty = [r["SOURCE_VALUE"] for r in rows if not str(r.get("TARGET_LABEL", "")).strip()]
    if empty:
        raise HTTPException(422, f"These values have no target label yet: {', '.join(empty[:5])}")

    existing_body: dict = {}
    art = None
    if req.artefact_id:
        try:
            art = repo.get_artefact(s, req.artefact_id)
            existing_body = repo.resolve_ref(s, req.artefact_id, None).body or {}
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
        if art.kind != "tco":
            raise HTTPException(409, f"Artefact '{art.name}' is a {art.kind}, not a tco.")
        # The environment that actually owns this artefact — never a
        # separately-supplied query-string `env` — so appending to another
        # environment's TCO can't be done just by knowing its id.
        _check_env_capability(s, user, art.environment, "tco.append")
    else:
        _check_env_capability(s, user, req.environment or repo.DEFAULT_ENV, "tco.append")
    existing_csv = existing_body.get("csv", "")
    target_sources: dict = existing_body.get("target_sources") or {}

    # A type with a target_source constrains what TARGET_LABEL may be to
    # whatever that source's query actually returns — checked here, not only
    # offered as a dropdown, so a direct API call cannot slip past it either.
    if target_sources:
        from app.services import tco_target_service
        by_type: dict[str, list[dict]] = {}
        for r in rows:
            by_type.setdefault(str(r.get("TYPE", "")), []).append(r)
        for type_, trows in by_type.items():
            src = target_sources.get(type_)
            if not src:
                continue
            try:
                allowed = {v.upper() for v in tco_target_service.resolve_allowed_values(
                    s, src["dataset_id"], src["query"])}
            except tco_target_service.BadTargetSource as e:
                raise HTTPException(422, f"Source de valeurs pour le type « {type_} » illisible : {e}")
            bad = sorted({r["TARGET_LABEL"] for r in trows
                         if r["TARGET_LABEL"].strip().upper() not in allowed})
            if bad:
                raise HTTPException(
                    409, f"Pour le type « {type_} », ces libellés cible ne sont pas dans la "
                        f"liste autorisée : {', '.join(bad[:5])}.")

    # The stored CSV's delimiter is whatever it was written with — a comma
    # from the table builder, a semicolon from an older upload — so it is
    # re-read through the same auto-detecting parser used everywhere else,
    # never a hardcoded separator that would silently mis-split one of them.
    if existing_csv.strip():
        try:
            old = TcoService().load_tco(existing_csv.encode("utf-8"))
        except ValueError as e:
            raise HTTPException(409, f"Table de correspondance existante illisible : {e}")
    else:
        old = pd.DataFrame()
    add = pd.DataFrame([{k: v for k, v in r.items()
                         if k in ("TYPE", "SOURCE_VALUE", "TARGET_LABEL")} for r in rows])
    merged = pd.concat([old, add], ignore_index=True) if len(old) else add
    keys = [c for c in ("TYPE", "SOURCE_VALUE") if c in merged.columns]
    if keys:
        merged = merged.drop_duplicates(subset=keys, keep="last")
    csv_text = merged.fillna("").to_csv(sep=";", index=False)

    new_body = {"csv": csv_text, **({"target_sources": target_sources} if target_sources else {})}
    if art is not None:
        ver = repo.add_version(s, art.id, new_body,
                               note=f"+{len(rows)} correspondance(s)")
        aid, version = art.id, ver.version_no
    else:
        if not req.name.strip():
            raise HTTPException(422, "Name the correspondence table to create it.")
        ver = repo.create_artefact(s, "tco", req.name.strip(), {"csv": csv_text},
                                   environment=req.environment or None)
        aid, version = ver.artefact_id, ver.version_no
    commit(s)
    return {"artefact_id": aid, "version_no": version, "rows": int(len(merged)),
            "added": len(rows)}
