"""
Flow graph routes — validate, run, and expose a flow as an API.

The last one is the point the whole design was building towards: a graph that
declares named parameters and yields one output is *already* an API. Calling
`POST /api/graphs/{id}/call` with a JSON body simply supplies those parameters
and returns the output node's records. No separate "publish" machinery, no
second execution path — the route is a thin shell over the same runner the
editor uses, which is what keeps what you test and what you serve identical.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.auth_routes import require_user
from app.flow_graph import FlowGraph, graph_from_yaml, graph_to_yaml
from app.services import pivot_service
from app.services.flow_runner import FlowError, RunContext, run_graph

router = APIRouter(prefix="/api/graphs", tags=["graphs"])


def _check_run_capability(s: Session, user, environment: str) -> None:
    """Check the caller's role in the environment the run actually executes
    as (`req.environment`) — never a separately-supplied `env` query param,
    which would let anyone with `flow.run` in "default" execute a flow (and
    its variable resolution) as if they belonged to any other environment."""
    from app.services import auth_service as _auth
    from app.services import permissions as _perms
    if not getattr(user, "id", ""):
        return                                  # setup mode
    scope = environment or repo.DEFAULT_ENV
    role = _auth.role_in(s, user, scope)
    if not _perms.can(role, "flow.run"):
        spec = _perms.CAPABILITIES.get("flow.run", {})
        raise HTTPException(403, f"« {spec.get('label', 'flow.run')} » demande le rôle "
                                 f"'{spec.get('min', '?')}' dans '{scope}' "
                                 f"(vous êtes '{role or 'non-membre'}').")


class RunRequest(BaseModel):
    environment: str = ""
    yaml: Optional[str] = None
    graph_id: Optional[str] = None
    version: Optional[int] = None
    params: Dict[str, str] = Field(default_factory=dict)
    limit: int = 200


def _loaders(s: Session):
    """Give the runner the two resolvers it needs, without it importing the
    repository — the traversal stays independent of storage."""

    def load_graph(ref: str, version: Optional[int]) -> FlowGraph:
        art = repo.get_artefact(s, ref)
        if art.kind != "graph":
            raise ValueError(f"artefact '{art.name}' is a {art.kind}, not a graph")
        return FlowGraph(**repo.resolve_ref(s, ref, version).body)

    def load_artefact(ref: str, version: Optional[int]) -> dict:
        return repo.resolve_ref(s, ref, version).body

    return load_graph, load_artefact


def _resolve_graph(s: Session, req: RunRequest) -> FlowGraph:
    if req.yaml and req.yaml.strip():
        try:
            return graph_from_yaml(req.yaml)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Invalid flow: {e}")
    if req.graph_id:
        try:
            art = repo.get_artefact(s, req.graph_id)
            if art.kind != "graph":
                raise HTTPException(409, f"Artefact '{art.name}' is a {art.kind}, not a graph.")
            return FlowGraph(**repo.resolve_ref(s, req.graph_id, req.version).body)
        except repo.NotFound as e:
            raise HTTPException(404, str(e))
    raise HTTPException(422, "Provide `yaml` or `graph_id`.")


def _bind_params(graph: FlowGraph, given: Dict[str, Any]) -> Dict[str, str]:
    """Defaults, then what the caller supplied. A required parameter left empty
    is refused up front rather than surfacing as a puzzling failure three bricks
    later."""
    params = {p.name: p.default for p in graph.params}
    for k, v in (given or {}).items():
        params[str(k)] = "" if v is None else str(v)
    missing = [p.name for p in graph.params if p.required and not params.get(p.name)]
    if missing:
        raise HTTPException(422, f"Missing required parameter(s): {', '.join(missing)}")
    return params


def _preview(records: list, limit: int) -> dict:
    df = pivot_service.records_to_frame(records)
    head = df.head(max(1, limit))
    return {"columns": list(df.columns),
            "data": head.astype(str).values.tolist(),
            "total_rows": int(len(df)), "shown_rows": int(len(head))}


@router.post("/validate")
def validate_graph(req: RunRequest, s: Session = Depends(get_session)):
    """Check a graph without running it: cycles, dangling edges, duplicate ids,
    an ambiguous output. Everything that would make it unrunnable, reported
    while it is still being edited."""
    graph = _resolve_graph(s, req)
    try:
        order = graph.topological_order()
        output = graph.resolve_output()
    except ValueError as e:
        raise HTTPException(422, str(e))
    unknown = [n.id for n in graph.nodes if n.type not in
               __import__("app.services.flow_runner", fromlist=["BRICKS"]).BRICKS]
    return {"ok": not unknown, "order": order, "output": output,
            "nodes": len(graph.nodes), "edges": len(graph.edges),
            "params": [p.model_dump() for p in graph.params],
            "unknown_types": unknown}


@router.post("/run")
def run(req: RunRequest, s: Session = Depends(get_session),
        user=Depends(require_user)):
    """Run a flow and return a preview plus the per-node trace — how long each
    brick took and how much it produced, which is what makes a ten-node flow
    debuggable."""
    _check_run_capability(s, user, req.environment)
    graph = _resolve_graph(s, req)
    params = _bind_params(graph, req.params)
    # Every execution goes through the journal — including this one, launched
    # from the editor. An operations table that only sees scheduled runs would
    # miss precisely the runs being debugged.
    from app.ops_routes import run_and_record
    result, run = run_and_record(
        s, graph, params=params, environment=req.environment or "default",
        graph_id=req.graph_id or "", loaders=_loaders(s))
    return {"ok": True, "output": result["output"], "meta": result["meta"],
            "trace": result["trace"], "run_id": run.id,
            "preview": _preview(result["records"], req.limit)}


@router.post("/adopt")
def adopt(req: RunRequest, s: Session = Depends(get_session),
          user=Depends(require_user)):
    """
    Run a flow and open its output as an ordinary working session — the same
    bridge `POST /api/datasets/{id}/open` gives a stored table. A flow that
    ends in a `join`/`lookup`/`compute` (no sink) is how several sources get
    cross-referenced interactively: once adopted, Schéma & Règles, Calculs,
    Rapport and Correspondances apply exactly as they would to an uploaded
    file, because none of them know or care where a session came from.
    """
    _check_run_capability(s, user, req.environment)
    from app.dataset_routes import _table_preview
    from app.models import FileResponse
    from app.session import store

    graph = _resolve_graph(s, req)
    params = _bind_params(graph, req.params)
    from app.ops_routes import run_and_record
    result, _run = run_and_record(
        s, graph, params=params, environment=req.environment or "default",
        graph_id=req.graph_id or "", loaders=_loaders(s))

    df = pivot_service.records_to_frame(result["records"]).drop(columns=["_doc"], errors="ignore")
    if len(df) > 200_000:
        raise HTTPException(
            413, f"Le résultat contient {len(df)} lignes, au-delà de la limite "
                 f"de 200 000. Utilisez une brique `dataset_write` pour l'écrire par lots.")

    sid = store.create(s, df, file_type="FLOW", encoding="N/A", delimiter="N/A")
    return FileResponse(session_id=sid, type="FLOW", encoding="N/A",
                        delimiter="N/A", preview=_table_preview(df))


@router.post("/{graph_id}/call")
def call_as_api(graph_id: str,
                params: Dict[str, Any] = Body(default_factory=dict),
                s: Session = Depends(get_session)):
    """
    A stored flow, served as an endpoint.

    The request body supplies the flow's parameters and the answer is the output
    node's records as plain JSON. A flow assembled in the editor is therefore
    callable by anything that speaks HTTP, with no extra step — and because it
    runs through the same runner, what was tested is exactly what is served.
    """
    try:
        art = repo.get_artefact(s, graph_id)
        if art.kind != "graph":
            raise HTTPException(409, f"Artefact '{art.name}' is a {art.kind}, not a graph.")
        graph = FlowGraph(**repo.resolve_ref(s, graph_id, None).body)
    except repo.NotFound as e:
        raise HTTPException(404, str(e))

    bound = _bind_params(graph, params)
    from app.ops_routes import run_and_record
    result, _run = run_and_record(s, graph, params=bound,
                                  environment=getattr(graph, "environment", "") or "default",
                                  graph_id=graph_id, loaders=_loaders(s))

    rows: list = []
    for rec in result["records"]:
        head = rec.get("head") or {}
        items = rec.get("items") or []
        rows.extend([{**head, **it} for it in items] if items else ([head] if head else []))
    return {"flow": graph.name, "count": len(rows), "data": rows,
            **({"file": result["meta"]} if "content_base64" in (result["meta"] or {}) else {})}


@router.get("/bricks")
def list_bricks():
    """What a flow can be built from — the palette the editor draws."""
    from app.services.flow_runner import BRICKS
    from app.flow_graph import SINK_TYPES, SOURCE_TYPES
    return {"bricks": [
        {"type": t,
         "role": "source" if t in SOURCE_TYPES else ("sink" if t in SINK_TYPES else "transform")}
        for t in BRICKS]}
