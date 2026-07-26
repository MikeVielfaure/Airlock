"""
Connection points and the operations table.

Two halves of the same concern: variables *feed* an execution, the journal
*observes* it. They share a file because they share the question an operator
actually asks — "what did this run use, and what happened to it?"

The journal is written by `run_and_record`, which every entry point goes
through. A run that crashes is recorded exactly like one that succeeds: an
operations table that only lists successes is worse than none, since the runs
you need to find are the failed ones.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.auth_routes import require_capability
from app.db_models import FlowRun, FlowRunStep
from app.flow_graph import FlowGraph
from app.services.flow_runner import FlowError, RunContext, run_graph

router = APIRouter(prefix="/api", tags=["ops"])

MASK = "••••••"


# ══════════════════════════════════════════════════════════════════════
# Variables
# ══════════════════════════════════════════════════════════════════════
class VariableIn(BaseModel):
    name: str
    value: str = ""
    scope: str = "environment"          # global | environment | flow | brick
    environment: str = ""
    graph_id: str = ""
    node_id: str = ""
    secret: bool = False
    description: str = ""


def _var_out(v, reveal: bool = False) -> dict:
    return {"id": v.id, "name": v.name,
            "value": (v.value if (reveal or not v.secret) else MASK),
            "scope": v.scope, "environment": v.environment, "graph_id": v.graph_id,
            "node_id": v.node_id, "secret": v.secret, "description": v.description}


@router.get("/variables")
def list_variables(env: str = "", graph_id: str = "", s: Session = Depends(get_session)):
    """Everything that could apply to this context, most general first, so the
    override chain is readable at a glance."""
    return [_var_out(v) for v in repo.list_variables(s, environment=env or None,
                                                     graph_id=graph_id)]


@router.get("/variables/resolved")
def resolved_variables(env: str = "", graph_id: str = "", node_id: str = "",
                       s: Session = Depends(get_session)):
    """The flattened cascade — what a brick would actually see. Secrets are
    reported as present without their value, because "is it set?" is the useful
    question and the value is not."""
    values = repo.resolve_variables(s, environment=env, graph_id=graph_id, node_id=node_id)
    secrets = repo.secret_names(s)
    return {"variables": {k: (MASK if k in secrets else v) for k, v in values.items()},
            "secret_names": sorted(secrets & set(values))}


@router.post("/variables")
def upsert_variable(req: VariableIn, s: Session = Depends(get_session),
        _cap=Depends(require_capability("variables.write"))):
    try:
        v = repo.upsert_variable(s, name=req.name, value=req.value, scope=req.scope,
                                 environment=req.environment, graph_id=req.graph_id,
                                 node_id=req.node_id, secret=req.secret,
                                 description=req.description)
    except repo.Conflict as e:
        raise HTTPException(409, str(e))
    commit(s)
    return _var_out(v)


@router.delete("/variables/{variable_id}")
def delete_variable(variable_id: str, s: Session = Depends(get_session),
        _cap=Depends(require_capability("variables.read"))):
    try:
        repo.delete_variable(s, variable_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    commit(s)
    return {"deleted": variable_id}


# ══════════════════════════════════════════════════════════════════════
# Running with a journal
# ══════════════════════════════════════════════════════════════════════
def run_and_record(s: Session, graph: FlowGraph, *, params: Dict[str, str],
                   environment: str, graph_id: str = "",
                   snapshot: Optional[dict] = None, replay_mode: str = "",
                   replay_of: str = "", loaders=(None, None)) -> tuple[dict, FlowRun]:
    """
    Execute a flow and journal it, whatever the outcome.

    The row is written *before* the run starts, with status "running": a flow
    that hangs or dies mid-way is still visible in the table rather than
    vanishing without trace.
    """
    load_graph, load_artefact = loaders
    variables = repo.resolve_variables(s, environment=environment, graph_id=graph_id)
    secrets = {v.name: v.value for v in repo.list_variables(s, environment=environment,
                                                            graph_id=graph_id) if v.secret}

    run = FlowRun(graph_id=graph_id, graph_name=graph.name, environment=environment or "default",
                  status="running", params_json=dict(params),
                  graph_json=graph.model_dump(by_alias=True), snapshot_json={},
                  messages_json=[], replay_of=replay_of, replay_mode=replay_mode)
    s.add(run)
    s.flush()
    commit(s)                      # visible immediately, not only once finished

    ctx = RunContext(params=params, session=s, environment=environment or "default",
                     graph_id=graph_id, variables=variables,
                     snapshot=dict(snapshot or {}), replay_mode=replay_mode,
                     load_graph=load_graph, load_artefact=load_artefact)

    started = time.perf_counter()
    failed: Optional[FlowError] = None
    result: dict = {}
    try:
        result = run_graph(graph, ctx)
    except FlowError as e:
        failed = e
    except ValueError as e:
        failed = FlowError("", str(e))

    run.ms = int((time.perf_counter() - started) * 1000)
    run.finished_at = datetime.now(timezone.utc)
    run.messages_json = [{**m, "text": repo.mask_secrets(m["text"], secrets)}
                         for m in ctx.messages]
    # The snapshot is replayed later, so it must keep working data — but a
    # source that echoed a secret would persist it. Mask on the way in.
    run.snapshot_json = repo.mask_deep(dict(ctx.snapshot), secrets)

    for i, step in enumerate(ctx.trace):
        meta = repo.mask_deep({k: v for k, v in (step.get("meta") or {}).items()
                               if k != "content_base64"}, secrets)
        s.add(FlowRunStep(
            run_id=run.id, ordinal=i, node_id=step["node"], node_type=step.get("type", ""),
            label=step.get("label") or "", status="ok", ms=int(step.get("ms") or 0),
            records=int(step.get("records") or 0), rows=int(step.get("rows") or 0),
            message=repo.mask_secrets(str(meta.get("message", "")), secrets),
            meta_json=meta))

    if failed is not None:
        run.status = "error"
        run.error = repo.mask_secrets(str(failed), secrets)
        run.error_node = failed.node_id
        # The failing brick gets its own row, so the table shows where it stopped.
        s.add(FlowRunStep(run_id=run.id, ordinal=len(ctx.trace), node_id=failed.node_id,
                          node_type="", label=failed.label or "", status="error",
                          message=repo.mask_secrets(str(failed), secrets)))
        commit(s)
        raise HTTPException(422, f"Node '{failed.node_id}': {run.error}"
                            if failed.node_id else run.error)

    run.status = "success"
    run.rows_out = sum(len(r.get("items") or []) for r in result.get("records", []))
    commit(s)
    return result, run


class RunAndLogRequest(BaseModel):
    graph_id: Optional[str] = None
    yaml: Optional[str] = None
    environment: str = ""
    params: Dict[str, str] = Field(default_factory=dict)
    limit: int = 200


# ══════════════════════════════════════════════════════════════════════
# The operations table
# ══════════════════════════════════════════════════════════════════════
def _run_out(r: FlowRun, with_steps: bool = False) -> dict:
    out = {
        "id": r.id, "graph_id": r.graph_id, "graph_name": r.graph_name,
        "environment": r.environment, "status": r.status, "ms": r.ms,
        "rows_out": r.rows_out, "error": r.error, "error_node": r.error_node,
        "params": r.params_json or {}, "messages": r.messages_json or [],
        "replay_of": r.replay_of, "replay_mode": r.replay_mode,
        "started_at": r.started_at.isoformat() if r.started_at else "",
        "finished_at": r.finished_at.isoformat() if r.finished_at else "",
        # Whether the exact input is still available decides which replay modes
        # the UI may offer, so it is stated rather than guessed at.
        "has_snapshot": bool(r.snapshot_json),
    }
    if with_steps:
        out["steps"] = [{
            "ordinal": st.ordinal, "node_id": st.node_id, "type": st.node_type,
            "label": st.label, "status": st.status, "ms": st.ms,
            "records": st.records, "rows": st.rows, "message": st.message,
            "meta": st.meta_json or {},
        } for st in r.steps]
    return out


@router.get("/ops/runs")
def list_runs(status: str = "", env: str = "", graph_id: str = "", limit: int = 50,
              s: Session = Depends(get_session),
        _cap=Depends(require_capability("ops.read"))):
    """The operations table: what ran, what is running, what failed."""
    q = select(FlowRun)
    if status:
        q = q.where(FlowRun.status == status)
    if env:
        q = q.where(FlowRun.environment == env)
    if graph_id:
        q = q.where(FlowRun.graph_id == graph_id)
    rows = list(s.scalars(q.order_by(FlowRun.started_at.desc()).limit(max(1, min(limit, 500)))))
    from sqlalchemy import func
    counts = {st: int(s.scalar(select(func.count()).select_from(FlowRun)
                               .where(FlowRun.status == st)) or 0)
              for st in ("running", "success", "error")}
    return {"runs": [_run_out(r) for r in rows], "counts": counts}


@router.get("/ops/runs/{run_id}")
def get_run(run_id: str, s: Session = Depends(get_session)):
    r = s.get(FlowRun, run_id)
    if r is None:
        raise HTTPException(404, f"Run {run_id} not found.")
    return _run_out(r, with_steps=True)


class ReplayRequest(BaseModel):
    mode: str = "same_data"          # same_data | refetch
    params: Optional[Dict[str, str]] = None


@router.post("/ops/runs/{run_id}/replay")
def replay_run(run_id: str, req: ReplayRequest, s: Session = Depends(get_session),
        _cap=Depends(require_capability("flow.replay"))):
    """
    Run it again — two meanings, and the difference matters.

    `same_data` feeds the sources from the snapshot: the API is not called, the
    trigger is not re-checked, and the run that failed is reproduced exactly.
    That is what you want to confirm a fix against the payload that broke it,
    especially once that payload no longer exists upstream.

    `refetch` re-executes the sources for real: the API is called again, the
    trigger re-evaluated. That is what you want when the failure was transient
    or the upstream data has since been corrected.
    """
    old = s.get(FlowRun, run_id)
    if old is None:
        raise HTTPException(404, f"Run {run_id} not found.")
    if req.mode not in ("same_data", "refetch"):
        raise HTTPException(422, "mode must be 'same_data' or 'refetch'")
    if req.mode == "same_data" and not old.snapshot_json:
        raise HTTPException(409, "No input snapshot for that run — replay with 'refetch'.")

    try:
        graph = FlowGraph(**(old.graph_json or {}))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"The recorded flow is unreadable: {e}")

    def load_graph(ref: str, version):
        return FlowGraph(**repo.resolve_ref(s, ref, version).body)

    def load_artefact(ref: str, version):
        return repo.resolve_ref(s, ref, version).body

    result, run = run_and_record(
        s, graph, params=(req.params if req.params is not None else (old.params_json or {})),
        environment=old.environment, graph_id=old.graph_id,
        snapshot=(old.snapshot_json if req.mode == "same_data" else None),
        replay_mode=req.mode, replay_of=old.id,
        loaders=(load_graph, load_artefact))
    return {"ok": True, "run": _run_out(run, with_steps=True),
            "output": result.get("output", "")}
