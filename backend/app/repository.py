"""
repository.py
─────────────
The only module that talks to the ORM. Services and routes call these functions;
they never touch a Session directly. Keeping data access here means the
versioning invariants live in exactly one place:

  • create_version() only ever APPENDS — version_no = latest + 1, atomically.
  • no update path mutates an existing ArtefactVersion.body.
  • resolve_ref() turns (artefact_id, version_no|None) into a concrete version,
    treating None as "latest". Flows store refs; runs store the resolved ids.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db_models import (
    Artefact, ArtefactGrant, ArtefactVersion, Dataset, DatasetRow, DatasetWrite,
    Flow, FlowRun, DatasetGrant, FlowRunStep, Run, Variable,
)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


# ── artefacts ─────────────────────────────────────────────────────────
def create_artefact(s: Session, kind: str, name: str, body: dict,
                    description: str = "", note: str = "",
                    environment: Optional[str] = None,
                    derived_from: str = "",
                    derived_from_version: Optional[int] = None) -> ArtefactVersion:
    """Create an artefact and its first version (v1), inside one environment."""
    env = environment or DEFAULT_ENV
    existing = s.scalar(select(Artefact).where(
        Artefact.kind == kind, Artefact.name == name, Artefact.environment == env))
    if existing is not None:
        raise Conflict(f"A {kind} named '{name}' already exists in '{env}' (id {existing.id}).")
    art = Artefact(kind=kind, name=name, description=description,
                   environment=env, latest_version_no=1,
                   derived_from=derived_from or "",
                   derived_from_version=derived_from_version)
    s.add(art)
    s.flush()
    ver = ArtefactVersion(artefact_id=art.id, version_no=1, body=body, note=note)
    s.add(ver)
    s.flush()
    return ver


def add_version(s: Session, artefact_id: str, body: dict, note: str = "") -> ArtefactVersion:
    """Append a new immutable version. This is how you 'edit' an artefact."""
    art = s.get(Artefact, artefact_id)
    if art is None:
        raise NotFound(f"Artefact {artefact_id} not found.")
    art.latest_version_no += 1
    ver = ArtefactVersion(artefact_id=art.id, version_no=art.latest_version_no, body=body, note=note)
    s.add(ver)
    s.flush()
    return ver


def get_artefact(s: Session, artefact_id: str) -> Artefact:
    art = s.get(Artefact, artefact_id)
    if art is None:
        raise NotFound(f"Artefact {artefact_id} not found.")
    return art


DEFAULT_ENV = "default"


def list_artefacts(s: Session, kind: Optional[str] = None, include_archived: bool = False,
                   environment: Optional[str] = None) -> list[Artefact]:
    """
    Artefacts of one environment. `environment=None` means the default one, not
    "all of them": a query that forgets the scope must show *less*, never leak
    another environment's material. Pass "*" deliberately to see everything.
    """
    q = select(Artefact)
    if environment != "*":
        q = q.where(Artefact.environment == (environment or DEFAULT_ENV))
    if kind:
        q = q.where(Artefact.kind == kind)
    if not include_archived:
        q = q.where(Artefact.archived == False)  # noqa: E712
    return list(s.scalars(q.order_by(Artefact.updated_at.desc())))


def list_visible_artefacts(s: Session, kind: Optional[str] = None, include_archived: bool = False,
                           environment: Optional[str] = None) -> list[Artefact]:
    """
    Owned artefacts UNION artefacts explicitly granted to this environment.

    `list_artefacts` answers "what does X own" (used for name uniqueness, for
    admin screens about property); this answers "what can X work with" — the
    library an environment browses from, which also includes what another
    environment chose to share with it.
    """
    if environment == "*":
        return list_artefacts(s, kind, include_archived, environment="*")
    env = environment or DEFAULT_ENV
    owned_ids = {a.id for a in list_artefacts(s, kind, include_archived, environment=env)}
    granted_ids = {g.artefact_id for g in s.scalars(
        select(ArtefactGrant).where(ArtefactGrant.subject_kind == "environment",
                                    ArtefactGrant.subject == env,
                                    ArtefactGrant.permission == "read"))}
    all_ids = owned_ids | granted_ids
    if not all_ids:
        return []
    q = select(Artefact).where(Artefact.id.in_(all_ids))
    if kind:
        q = q.where(Artefact.kind == kind)
    if not include_archived:
        q = q.where(Artefact.archived == False)  # noqa: E712
    return list(s.scalars(q.order_by(Artefact.updated_at.desc())))


def resolve_ref(s: Session, artefact_id: str, version_no: Optional[int]) -> ArtefactVersion:
    """(artefact_id, version_no|None) → concrete ArtefactVersion. None = latest."""
    art = s.get(Artefact, artefact_id)
    if art is None:
        raise NotFound(f"Artefact {artefact_id} not found.")
    target = version_no if version_no is not None else art.latest_version_no
    ver = s.scalar(select(ArtefactVersion).where(
        ArtefactVersion.artefact_id == artefact_id,
        ArtefactVersion.version_no == target))
    if ver is None:
        raise NotFound(f"Version {target} of artefact {artefact_id} not found.")
    return ver


def get_version_by_id(s: Session, version_id: str) -> ArtefactVersion:
    ver = s.get(ArtefactVersion, version_id)
    if ver is None:
        raise NotFound(f"Version {version_id} not found.")
    return ver


def archive_artefact(s: Session, artefact_id: str) -> None:
    get_artefact(s, artefact_id).archived = True


# ── flows ─────────────────────────────────────────────────────────────
def create_flow(s: Session, **kw) -> Flow:
    name = kw.get("name")
    if s.scalar(select(Flow).where(Flow.name == name)) is not None:
        raise Conflict(f"A flow named '{name}' already exists.")
    # Validate referenced artefacts exist and have the right kind.
    _require_kind(s, kw["config_artefact_id"], "config")
    if kw.get("tco_artefact_id"):
        _require_kind(s, kw["tco_artefact_id"], "tco")
    if kw.get("computed_artefact_id"):
        _require_kind(s, kw["computed_artefact_id"], "computed")
    flow = Flow(**kw)
    s.add(flow)
    s.flush()
    return flow


def _require_kind(s: Session, artefact_id: str, kind: str) -> None:
    art = s.get(Artefact, artefact_id)
    if art is None:
        raise NotFound(f"Artefact {artefact_id} not found.")
    if art.kind != kind:
        raise Conflict(f"Artefact {artefact_id} is a '{art.kind}', expected '{kind}'.")


def get_flow(s: Session, flow_id: str) -> Flow:
    flow = s.get(Flow, flow_id)
    if flow is None:
        raise NotFound(f"Flow {flow_id} not found.")
    return flow


def list_flows(s: Session, include_archived: bool = False) -> list[Flow]:
    q = select(Flow)
    if not include_archived:
        q = q.where(Flow.archived == False)  # noqa: E712
    return list(s.scalars(q.order_by(Flow.updated_at.desc())))


def update_flow(s: Session, flow_id: str, **kw) -> Flow:
    flow = get_flow(s, flow_id)
    for k, v in kw.items():
        if v is not None and hasattr(flow, k):
            setattr(flow, k, v)
    s.flush()
    return flow


def archive_flow(s: Session, flow_id: str) -> None:
    get_flow(s, flow_id).archived = True


# ── runs ──────────────────────────────────────────────────────────────
def create_run(s: Session, **kw) -> Run:
    run = Run(**kw)
    s.add(run)
    s.flush()
    return run


def get_run(s: Session, run_id: str) -> Run:
    run = s.get(Run, run_id)
    if run is None:
        raise NotFound(f"Run {run_id} not found.")
    return run


def list_runs(s: Session, flow_id: Optional[str] = None, limit: int = 50) -> list[Run]:
    q = select(Run)
    if flow_id:
        q = q.where(Run.flow_id == flow_id)
    return list(s.scalars(q.order_by(Run.created_at.desc()).limit(limit)))


# ══════════════════════════════════════════════════════════════════════
# Datasets (v14)
# ══════════════════════════════════════════════════════════════════════
def create_dataset(s: Session, name: str, schema: dict, description: str = "",
                   environment: Optional[str] = None) -> Dataset:
    name = (name or "").strip()
    if not name:
        raise Conflict("Un nom de table est requis.")
    env = environment or DEFAULT_ENV
    if s.scalar(select(Dataset).where(Dataset.name == name,
                                      Dataset.environment == env)) is not None:
        raise Conflict(f"Une table nommée '{name}' existe déjà dans '{env}'.")
    ds = Dataset(name=name, description=description, schema_json=schema, environment=env)
    s.add(ds)
    s.flush()
    return ds


def get_dataset(s: Session, dataset_id: str) -> Dataset:
    ds = s.get(Dataset, dataset_id)
    if ds is None:
        raise NotFound(f"Table {dataset_id} introuvable.")
    return ds


def list_environments(s: Session) -> list[str]:
    """Every environment that holds something, has a profile, or has members —
    a profile-only environment (no artefact/table yet) must still show up,
    same union as auth_routes.overview()."""
    from app.db_models import EnvironmentProfile, Membership

    envs = {e for (e,) in s.execute(select(Artefact.environment).distinct())}
    envs |= {e for (e,) in s.execute(select(Dataset.environment).distinct())}
    envs |= {p.name for p in s.scalars(select(EnvironmentProfile))}
    envs |= {e for (e,) in s.execute(select(Membership.environment).distinct())}
    envs.discard(None)
    envs.add(DEFAULT_ENV)
    return sorted(envs)


def find_dataset_by_name(s: Session, name: str, environment: Optional[str] = None) -> Optional[Dataset]:
    return s.scalar(select(Dataset).where(
        Dataset.name == (name or "").strip(),
        Dataset.environment == (environment or DEFAULT_ENV)))


def list_datasets(s: Session, include_archived: bool = False,
                  environment: Optional[str] = None) -> list[Dataset]:
    q = select(Dataset)
    if environment != "*":
        q = q.where(Dataset.environment == (environment or DEFAULT_ENV))
    if not include_archived:
        q = q.where(Dataset.archived.is_(False))
    return list(s.scalars(q.order_by(Dataset.name)))


def archive_dataset(s: Session, dataset_id: str) -> None:
    get_dataset(s, dataset_id).archived = True


def count_rows(s: Session, dataset_id: str) -> int:
    return int(s.scalar(select(func.count()).select_from(DatasetRow)
                        .where(DatasetRow.dataset_id == dataset_id)) or 0)


def delete_all_rows(s: Session, dataset_id: str) -> int:
    n = count_rows(s, dataset_id)
    s.execute(delete(DatasetRow).where(DatasetRow.dataset_id == dataset_id))
    return n


def next_ordinal(s: Session, dataset_id: str) -> int:
    """Where the next appended row goes — one past the current tail."""
    top = s.scalar(select(func.max(DatasetRow.ordinal))
                   .where(DatasetRow.dataset_id == dataset_id))
    return int(top) + 1 if top is not None else 0


def insert_rows(s: Session, dataset_id: str, payload: list[dict], batch: int = 1000,
                start_ordinal: Optional[int] = None) -> int:
    """Bulk insert in batches — a naive per-row add() dies on large files.
    Each row gets an increasing ordinal so the table reads back in the order
    it was written."""
    now = datetime.now(timezone.utc)
    ordinal = next_ordinal(s, dataset_id) if start_ordinal is None else start_ordinal
    total = 0
    for i in range(0, len(payload), batch):
        chunk = payload[i:i + batch]
        s.bulk_insert_mappings(DatasetRow, [
            {"id": uuid.uuid4().hex, "dataset_id": dataset_id,
             "ordinal": ordinal + i + j,
             "key_hash": r["key_hash"], "data": r["data"],
             "created_at": now, "updated_at": now}
            for j, r in enumerate(chunk)])
        total += len(chunk)
    return total


def existing_key_map(s: Session, dataset_id: str, hashes: list[str]) -> dict[str, str]:
    """{key_hash: row_id} for the incoming keys — one query per batch, not per row."""
    out: dict[str, str] = {}
    uniq = [h for h in dict.fromkeys(hashes) if h]
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        rows = s.execute(
            select(DatasetRow.key_hash, DatasetRow.id)
            .where(DatasetRow.dataset_id == dataset_id, DatasetRow.key_hash.in_(chunk))
        ).all()
        for kh, rid in rows:
            out[kh] = rid
    return out


def update_rows(s: Session, updates: list[dict], batch: int = 1000) -> int:
    """updates: [{id, data}] — bulk update by primary key."""
    now = datetime.now(timezone.utc)
    total = 0
    for i in range(0, len(updates), batch):
        chunk = updates[i:i + batch]
        s.bulk_update_mappings(DatasetRow, [
            {"id": u["id"], "data": u["data"], "updated_at": now} for u in chunk])
        total += len(chunk)
    return total


def read_rows(s: Session, dataset_id: str, offset: int = 0, limit: int = 100) -> list[DatasetRow]:
    return list(s.scalars(
        select(DatasetRow).where(DatasetRow.dataset_id == dataset_id)
        .order_by(DatasetRow.ordinal, DatasetRow.id).offset(offset).limit(limit)))


def log_write(s: Session, **kw) -> DatasetWrite:
    w = DatasetWrite(**kw)
    s.add(w)
    s.flush()
    return w


def list_writes(s: Session, dataset_id: Optional[str] = None, limit: int = 30) -> list[DatasetWrite]:
    q = select(DatasetWrite)
    if dataset_id:
        q = q.where(DatasetWrite.dataset_id == dataset_id)
    return list(s.scalars(q.order_by(DatasetWrite.created_at.desc()).limit(limit)))


# ══════════════════════════════════════════════════════════════════
# VARIABLES — connection points, resolved by scope
# ══════════════════════════════════════════════════════════════════
SCOPES = ("global", "environment", "flow", "brick")
_MASK = "••••••"


def upsert_variable(s: Session, *, name: str, value: str, scope: str = "environment",
                    environment: str = "", graph_id: str = "", node_id: str = "",
                    secret: bool = False, description: str = "") -> Variable:
    name = (name or "").strip()
    if not name:
        raise Conflict("A variable needs a name.")
    if scope not in SCOPES:
        raise Conflict(f"Unknown scope '{scope}' (use {', '.join(SCOPES)}).")
    # Narrower scopes must say what they narrow: a flow variable with no flow
    # would silently behave as a global one.
    if scope == "environment" and not environment:
        environment = DEFAULT_ENV
    if scope in ("flow", "brick") and not graph_id:
        raise Conflict(f"A {scope}-scoped variable needs a graph_id.")
    if scope == "brick" and not node_id:
        raise Conflict("A brick-scoped variable needs a node_id.")
    if scope == "global":
        environment = graph_id = node_id = ""

    existing = s.scalar(select(Variable).where(
        Variable.name == name, Variable.scope == scope,
        Variable.environment == environment, Variable.graph_id == graph_id,
        Variable.node_id == node_id))
    if existing is not None:
        existing.value = value
        existing.secret = secret
        existing.description = description
        return existing
    v = Variable(name=name, value=value, scope=scope, environment=environment,
                 graph_id=graph_id, node_id=node_id, secret=secret,
                 description=description)
    s.add(v)
    s.flush()
    return v


def list_variables(s: Session, environment: Optional[str] = None,
                   graph_id: str = "") -> list[Variable]:
    """Everything that could apply to this context, most general first."""
    env = environment or DEFAULT_ENV
    q = select(Variable).where(
        (Variable.scope == "global")
        | ((Variable.scope == "environment") & (Variable.environment == env))
        | ((Variable.scope.in_(("flow", "brick"))) & (Variable.graph_id == graph_id))
    )
    order = {"global": 0, "environment": 1, "flow": 2, "brick": 3}
    return sorted(s.scalars(q), key=lambda v: (order.get(v.scope, 9), v.name))


def delete_variable(s: Session, variable_id: str) -> None:
    v = s.get(Variable, variable_id)
    if v is None:
        raise NotFound(f"Variable {variable_id} not found.")
    s.delete(v)


def resolve_variables(s: Session, *, environment: str = "", graph_id: str = "",
                      node_id: str = "") -> dict:
    """
    Flatten the cascade into one name → value map for this exact context.

    Precedence is most-specific-wins: brick beats flow beats environment beats
    global. Applying them in that order means a later write simply overwrites an
    earlier one, which is both the simplest implementation and the one whose
    behaviour is easiest to predict.
    """
    out: dict[str, str] = {}
    for scope in ("global", "environment", "flow", "brick"):
        for v in s.scalars(select(Variable).where(Variable.scope == scope)):
            if scope == "environment" and v.environment != (environment or DEFAULT_ENV):
                continue
            if scope in ("flow", "brick") and v.graph_id != graph_id:
                continue
            # Asking for the flow-level view must NOT show a brick's override:
            # without this, a variable specialised for one node would appear to
            # apply to the whole flow.
            if scope == "brick" and (not node_id or v.node_id != node_id):
                continue
            out[v.name] = v.value
    return out


def secret_names(s: Session) -> set:
    """Names whose values must never reach a screen or a journal."""
    return {v.name for v in s.scalars(select(Variable).where(Variable.secret.is_(True)))}


def mask_deep(value, secrets: dict):
    """
    Mask secrets anywhere inside a nested structure.

    Masking only the obvious field is not enough: a node's metadata carries the
    *resolved* message, so a key substituted into a URL would reach the journal
    through the very record meant to help debug it. Everything written to the
    journal goes through here.
    """
    if isinstance(value, str):
        return mask_secrets(value, secrets)
    if isinstance(value, dict):
        return {k: mask_deep(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_deep(v, secrets) for v in value]
    return value


def mask_secrets(text: str, secrets: dict) -> str:
    """Replace every secret value found in a string. Applied to messages, errors
    and logged config so a key pasted into a URL cannot leak through the very
    journal meant to help debug it."""
    if not text:
        return text
    for value in secrets.values():
        if value and len(str(value)) >= 4:
            text = text.replace(str(value), _MASK)
    return text


# ══════════════════════════════════════════════════════════════════
# TABLE PERMISSIONS — who may do what on one table
# ══════════════════════════════════════════════════════════════════
PERM_RANK = {"read": 0, "write": 1, "manage": 2}


def grant_on_dataset(s: Session, dataset_id: str, *, subject: str,
                     subject_kind: str = "user", permission: str = "read",
                     granted_by: str = "") -> DatasetGrant:
    if permission not in PERM_RANK:
        raise Conflict(f"Unknown permission '{permission}'.")
    if subject_kind not in ("user", "role", "environment"):
        raise Conflict(f"Unknown subject kind '{subject_kind}'.")
    g = s.scalar(select(DatasetGrant).where(
        DatasetGrant.dataset_id == dataset_id,
        DatasetGrant.subject_kind == subject_kind, DatasetGrant.subject == subject))
    if g is None:
        g = DatasetGrant(dataset_id=dataset_id, subject_kind=subject_kind,
                         subject=subject, permission=permission, granted_by=granted_by)
        s.add(g)
    else:
        g.permission = permission
    s.flush()
    return g


def revoke_on_dataset(s: Session, dataset_id: str, subject: str,
                      subject_kind: str = "user") -> None:
    g = s.scalar(select(DatasetGrant).where(
        DatasetGrant.dataset_id == dataset_id,
        DatasetGrant.subject_kind == subject_kind, DatasetGrant.subject == subject))
    if g is not None:
        s.delete(g)


def list_grants(s: Session, dataset_id: str) -> list[DatasetGrant]:
    return list(s.scalars(select(DatasetGrant)
                          .where(DatasetGrant.dataset_id == dataset_id)))


def dataset_permission(s: Session, ds, user, env_role: Optional[str],
                       environment: str = "") -> Optional[str]:
    """
    The strongest permission this person has on this table.

    The rules, in order, and each exists for a reason:
      * no account yet (setup) → manage, so a fresh install is usable;
      * the owner always keeps manage — building a table and losing access to it
        would be absurd;
      * an environment admin gets manage, otherwise a table could outlive
        everyone able to administer it;
      * explicit grants, by user, by role, then by environment (an environment
        as a whole, granted read access to a table it does not own);
      * finally the default: a personal table is private, a business table is
        readable by the environment. Making that a flag rather than a convention
        means the choice is made deliberately when the table is created.
    """
    if not getattr(user, "id", ""):
        return "manage"
    if ds.owner_id and ds.owner_id == user.id:
        return "manage"
    if env_role == "admin":
        return "manage"

    best: Optional[str] = None
    for g in list_grants(s, ds.id):
        applies = ((g.subject_kind == "user" and g.subject == user.id)
                   or (g.subject_kind == "role" and env_role and g.subject == env_role)
                   or (g.subject_kind == "environment" and environment and g.subject == environment))
        if applies and (best is None or PERM_RANK[g.permission] > PERM_RANK[best]):
            best = g.permission
    if best is not None:
        return best
    return "read" if ds.is_managed else None


def can_on_dataset(permission: Optional[str], needed: str) -> bool:
    if permission is None:
        return False
    return PERM_RANK.get(permission, -1) >= PERM_RANK.get(needed, 99)


# ══════════════════════════════════════════════════════════════════
# ARTEFACT GRANTS — one environment reading another's artefact
# ══════════════════════════════════════════════════════════════════
def grant_artefact_to_environment(s: Session, artefact_id: str, *, environment: str,
                                  granted_by: str = "") -> ArtefactGrant:
    """
    v1 is deliberately narrow: `subject_kind` is always "environment" and
    `permission` is always "read" — write stays with the owning environment,
    so there is never a question of who may create the next version.
    """
    g = s.scalar(select(ArtefactGrant).where(
        ArtefactGrant.artefact_id == artefact_id,
        ArtefactGrant.subject_kind == "environment", ArtefactGrant.subject == environment))
    if g is None:
        g = ArtefactGrant(artefact_id=artefact_id, subject_kind="environment",
                          subject=environment, permission="read", granted_by=granted_by)
        s.add(g)
        s.flush()
    return g


def revoke_artefact_from_environment(s: Session, artefact_id: str, environment: str) -> None:
    g = s.scalar(select(ArtefactGrant).where(
        ArtefactGrant.artefact_id == artefact_id,
        ArtefactGrant.subject_kind == "environment", ArtefactGrant.subject == environment))
    if g is not None:
        s.delete(g)


def list_artefact_grants(s: Session, artefact_id: str) -> list[ArtefactGrant]:
    return list(s.scalars(select(ArtefactGrant)
                          .where(ArtefactGrant.artefact_id == artefact_id)))


def artefact_visible(s: Session, art, environment: str) -> bool:
    """
    Whether `environment` may read this artefact: owns it, has been granted it,
    or has pinned it as its own config/tco through its EnvironmentProfile — a
    profile's pin is itself a form of authorization, or a "locked configuration"
    environment could never actually read the configuration it is locked to.
    """
    from app.db_models import EnvironmentProfile

    if environment == "*":
        return True
    env = environment or DEFAULT_ENV
    if art.environment == env:
        return True
    granted = s.scalar(select(ArtefactGrant).where(
        ArtefactGrant.artefact_id == art.id, ArtefactGrant.subject_kind == "environment",
        ArtefactGrant.subject == env, ArtefactGrant.permission == "read"))
    if granted is not None:
        return True
    prof = s.get(EnvironmentProfile, env)
    if prof is not None and art.id in (prof.config_artefact_id, prof.tco_artefact_id):
        return True
    return False


def lineage(s: Session, artefact_id: str) -> dict:
    """
    Where an artefact comes from, and what came from it.

    Ancestors are walked with a guard: a cycle should be impossible (one only
    ever derives from something that already exists) but a corrupted row must
    not spin forever.
    """
    art = get_artefact(s, artefact_id)
    ancestors: list[dict] = []
    seen = {artefact_id}
    cur = art
    while cur.derived_from and cur.derived_from not in seen:
        seen.add(cur.derived_from)
        parent = s.get(Artefact, cur.derived_from)
        if parent is None:
            # The ancestor was deleted. Say so rather than silently ending the
            # chain, because "derived from something gone" is worth knowing.
            ancestors.append({"id": cur.derived_from, "name": "(supprimé)",
                              "version_no": cur.derived_from_version, "missing": True})
            break
        ancestors.append({"id": parent.id, "name": parent.name, "kind": parent.kind,
                          "version_no": cur.derived_from_version,
                          "latest_version_no": parent.latest_version_no,
                          "missing": False})
        cur = parent
    children = [{"id": c.id, "name": c.name, "kind": c.kind,
                 "from_version": c.derived_from_version,
                 "latest_version_no": c.latest_version_no}
                for c in s.scalars(select(Artefact)
                                   .where(Artefact.derived_from == artefact_id))]
    return {"id": art.id, "name": art.name, "kind": art.kind,
            "ancestors": ancestors, "children": children}


# ══════════════════════════════════════════════════════════════════
# ENVIRONMENT DELETION — migrate what's chosen, then profile-only or cascade
# ══════════════════════════════════════════════════════════════════
def _dedup_name(exists: "callable", name: str) -> str:
    """`name` if free in the target, else `name-migré`, `name-migré-2`, … A
    migration must never fail over a naming collision — the admin asked to
    move data, not to be told no."""
    if not exists(name):
        return name
    candidate = f"{name}-migré"
    n = 1
    while exists(candidate):
        n += 1
        candidate = f"{name}-migré-{n}"
    return candidate


def move_artefact_to_environment(s: Session, artefact_id: str, from_env: str, to_env: str) -> Artefact:
    art = get_artefact(s, artefact_id)
    if art.environment != from_env:
        raise Conflict(f"L'artefact {artefact_id} n'appartient pas à '{from_env}'.")

    def _exists(name: str) -> bool:
        return s.scalar(select(Artefact).where(
            Artefact.kind == art.kind, Artefact.name == name,
            Artefact.environment == to_env)) is not None

    art.environment = to_env
    art.name = _dedup_name(_exists, art.name)
    s.flush()   # autoflush is off: a later query in this request must see the move
    return art


def move_dataset_to_environment(s: Session, dataset_id: str, from_env: str, to_env: str) -> Dataset:
    ds = get_dataset(s, dataset_id)
    if ds.environment != from_env:
        raise Conflict(f"La table {dataset_id} n'appartient pas à '{from_env}'.")

    def _exists(name: str) -> bool:
        return s.scalar(select(Dataset).where(
            Dataset.name == name, Dataset.environment == to_env)) is not None

    ds.environment = to_env
    ds.name = _dedup_name(_exists, ds.name)
    s.flush()
    return ds


def move_crypto_key_to_environment(s: Session, key_id: str, from_env: str, to_env: str):
    from app.db_models import CryptoKey

    key = s.get(CryptoKey, key_id)
    if key is None:
        raise NotFound(f"Key {key_id} not found.")
    if key.environment != from_env:
        raise Conflict(f"La clé {key_id} n'appartient pas à '{from_env}'.")

    def _exists(name: str) -> bool:
        return s.scalar(select(CryptoKey).where(
            CryptoKey.name == name, CryptoKey.environment == to_env)) is not None

    key.environment = to_env
    key.name = _dedup_name(_exists, key.name)
    s.flush()
    return key


def _blocking_flows(s: Session, artefact_id: str) -> list[Flow]:
    """Non-archived flows that still point at this artefact — a hard delete
    would otherwise leave them referencing something gone."""
    return list(s.scalars(select(Flow).where(
        Flow.archived == False,  # noqa: E712
        (Flow.config_artefact_id == artefact_id) | (Flow.tco_artefact_id == artefact_id)
        | (Flow.computed_artefact_id == artefact_id))))


def delete_environment_profile_only(s: Session, name: str) -> None:
    """The environment becomes orphaned: whatever it still owns stays exactly
    as it is — a profile describes exposure and membership, not data."""
    from app.db_models import EnvironmentProfile, Membership

    prof = s.get(EnvironmentProfile, name)
    if prof is not None:
        s.delete(prof)
    s.execute(delete(Membership).where(Membership.environment == name))
    s.execute(delete(Variable).where(Variable.scope == "environment",
                                     Variable.environment == name))


def delete_environment_cascade(s: Session, name: str) -> None:
    """
    Everything `delete_environment_profile_only` does, plus a hard delete of
    whatever this environment still owns.

    FlowRun and RevealEvent are never purged: they are an audit trail, and
    losing them the moment their subject disappears would be worse than
    keeping a record that now points at a name nobody occupies any more.

    Grants and versions/rows are deleted explicitly rather than left to the
    database's ON DELETE CASCADE — SQLite (dev and tests) never turns
    `PRAGMA foreign_keys` on, so those constraints are inert there; only the
    ORM relationships (`cascade="all, delete-orphan"`) fire on every engine.
    """
    from app.db_models import CryptoKey, EnvironmentProfile

    delete_environment_profile_only(s, name)

    remaining_artefacts = list_artefacts(s, environment=name, include_archived=True)
    for art in remaining_artefacts:
        blockers = _blocking_flows(s, art.id)
        if blockers:
            raise Conflict(f"« {art.name} » est encore utilisé par le flux "
                           f"« {blockers[0].name} » : archivez-le d'abord.")
        pinning = list(s.scalars(select(EnvironmentProfile).where(
            (EnvironmentProfile.config_artefact_id == art.id)
            | (EnvironmentProfile.tco_artefact_id == art.id))))
        if pinning:
            raise Conflict(f"« {art.name} » est imposé par le profil de "
                           f"« {pinning[0].name} » : détachez-le d'abord.")
    for art in remaining_artefacts:
        s.execute(delete(ArtefactGrant).where(ArtefactGrant.artefact_id == art.id))
        s.delete(art)   # cascades to ArtefactVersion via the ORM relationship

    remaining_datasets = list_datasets(s, environment=name, include_archived=True)
    for ds in remaining_datasets:
        s.execute(delete(DatasetGrant).where(DatasetGrant.dataset_id == ds.id))
        s.delete(ds)    # cascades to DatasetRow via the ORM relationship

    for key in s.scalars(select(CryptoKey).where(CryptoKey.environment == name)):
        s.delete(key)   # cascades to KeyHolder via the ORM relationship
