"""
A flow served as a real API.

Flows were already callable over HTTP — but at `/api/graphs/{uuid}/call`, which
is an address nobody can read, remember or document. The contract-first idea
worth borrowing from integration platforms is not the platform: it is that an
API **declares** itself before it is consumed.

So three things, and no new storage for any of them:

  * **The route is the artefact's address.** `/api/run/rh/commandes-partenaire`
    resolves by environment and name, which are already unique together. A
    publication registry would be one more thing to keep in step with reality.
  * **The contract comes from the graph.** Parameters already carry a name, a
    default, whether they are required — plus now a type and a description. That
    is an OpenAPI document, so it is generated rather than written by hand and
    left to drift.
  * **The version can be pinned in the call**, because artefacts are versioned:
    `?version=3` serves that version forever, whatever happens to the flow after.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import repository as repo
from app.auth_routes import current_env, require_capability, require_user
from app.db import get_session
from app.flow_graph import FlowGraph

router = APIRouter(prefix="/api/run", tags=["published flows"])

_JSON_TYPE = {"string": "string", "number": "number",
              "boolean": "boolean", "date": "string"}


def _resolve(s: Session, env: str, name: str, version: Optional[int]) -> FlowGraph:
    found = [a for a in repo.list_artefacts(s, "graph", environment=env)
             if a.name == name]
    if not found:
        raise HTTPException(404, f"Aucun flux nommé « {name} » dans « {env} ».")
    art = found[0]
    try:
        return FlowGraph(**repo.resolve_ref(s, art.id, version).body), art
    except repo.NotFound as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Le flux « {name} » est illisible : {e}")


def _coerce(graph: FlowGraph, given: Dict[str, Any]) -> Dict[str, str]:
    """
    Check the body against the declared contract, then hand everything on as
    text — which is what the engine works with.

    Checking rather than silently accepting is the point of declaring a type: a
    caller who sends "abc" where a number is expected learns it here, not three
    bricks later in a message about a failed expression.
    """
    out: Dict[str, str] = {}
    problems = []
    declared = {p.name: p for p in graph.params}

    for name, param in declared.items():
        raw = given.get(name, param.default)
        if raw is None or str(raw).strip() == "":
            if param.required:
                problems.append(f"« {name} » est obligatoire")
            out[name] = ""
            continue
        text = str(raw)
        if param.type == "number":
            try:
                float(text.replace(",", "."))
            except ValueError:
                problems.append(f"« {name} » doit être un nombre (reçu : {text!r})")
        elif param.type == "boolean":
            if text.lower() not in ("true", "false", "1", "0", "oui", "non"):
                problems.append(f"« {name} » doit être un booléen (reçu : {text!r})")
        elif param.type == "date":
            import re
            if not re.match(r"^\d{4}-?\d{2}-?\d{2}$", text):
                problems.append(f"« {name} » doit être une date AAAA-MM-JJ (reçu : {text!r})")
        out[name] = text

    # An unknown field is a mistake worth naming: silently ignoring it is how a
    # caller spends an afternoon wondering why their parameter has no effect.
    unknown = [k for k in (given or {}) if k not in declared]
    if unknown:
        problems.append(f"paramètre(s) inconnu(s) : {', '.join(sorted(unknown))}")

    if problems:
        raise HTTPException(422, "Contrat non respecté — " + " ; ".join(problems))
    return out


def _openapi_for(graph: FlowGraph, env: str, name: str) -> dict:
    """The document a consumer needs, generated from the graph itself."""
    props, required = {}, []
    for p in graph.params:
        props[p.name] = {"type": _JSON_TYPE.get(p.type, "string"),
                         "description": p.description or "",
                         **({"format": "date"} if p.type == "date" else {}),
                         **({"default": p.default} if p.default else {}),
                         **({"example": p.example} if p.example else {})}
        if p.required:
            required.append(p.name)

    path = f"/api/run/{env}/{name}"
    return {
        "openapi": "3.0.3",
        "info": {"title": graph.name or name,
                 "description": graph.description or "",
                 "version": "1"},
        "paths": {path: {"post": {
            "summary": graph.name or name,
            "description": graph.description or "",
            "operationId": name.replace("-", "_"),
            "parameters": [{"name": "version", "in": "query", "required": False,
                            "schema": {"type": "integer"},
                            "description": "Épingler une version du flux."}],
            "requestBody": {"required": bool(required), "content": {"application/json": {
                "schema": {"type": "object", "properties": props,
                           **({"required": required} if required else {})}}}},
            "responses": {
                "200": {"description": "Résultat du flux",
                        "content": {"application/json": {"schema": {"type": "object",
                            "properties": {
                                "flow": {"type": "string"},
                                "count": {"type": "integer"},
                                "data": {"type": "array",
                                         "items": {"type": "object"}}}}}}},
                "422": {"description": "Contrat non respecté, ou échec d'une brique"},
                "404": {"description": "Flux inconnu dans cet environnement"}},
        }}},
    }


@router.get("/{env}/{name}/openapi.json")
def flow_openapi(env: str, name: str, version: Optional[int] = None,
                 scope: str = Depends(current_env),
                 s: Session = Depends(get_session)):
    """The contract, generated — never written by hand, so it cannot drift."""
    graph, _art = _resolve(s, scope, name, version)
    return _openapi_for(graph, scope, name)


@router.get("/{env}")
def list_published(env: str, scope: str = Depends(current_env),
                   s: Session = Depends(get_session)):
    """Every flow callable in this environment, with its address and contract."""
    out = []
    for a in repo.list_artefacts(s, "graph", environment=scope):
        try:
            g = FlowGraph(**repo.resolve_ref(s, a.id, None).body)
        except Exception:  # noqa: BLE001 — an unreadable flow must not hide the rest
            continue
        out.append({
            "name": a.name, "label": g.name, "description": g.description,
            "version": a.latest_version_no,
            "url": f"/api/run/{scope}/{a.name}",
            "openapi": f"/api/run/{scope}/{a.name}/openapi.json",
            "params": [{"name": p.name, "type": p.type, "required": p.required,
                        "default": p.default, "description": p.description}
                       for p in g.params],
        })
    return {"environment": scope, "flows": out}


@router.post("/{env}/{name}")
def call_published(env: str, name: str, request: Request,
                   body: Dict[str, Any] = Body(default_factory=dict),
                   version: Optional[int] = None,
                   scope: str = Depends(current_env),
                   user=Depends(require_user),
                   _cap=Depends(require_capability("flow.run")),
                   s: Session = Depends(get_session)):
    """
    Call a flow at a readable address.

    The same runner as the editor, and the same journal: a call made from
    production is a run like any other, visible in the operations table and
    replayable. That is what keeps "what was tested" and "what is served"
    the same thing.
    """
    graph, art = _resolve(s, scope, name, version)
    params = _coerce(graph, body or {})

    def load_graph(ref: str, v):
        return FlowGraph(**repo.resolve_ref(s, ref, v).body)

    def load_artefact(ref: str, v):
        return repo.resolve_ref(s, ref, v).body

    from app.ops_routes import run_and_record
    result, run = run_and_record(s, graph, params=params, environment=scope,
                                 graph_id=art.id,
                                 loaders=(load_graph, load_artefact))

    rows: list = []
    for rec in result["records"]:
        head = rec.get("head") or {}
        items = rec.get("items") or []
        rows.extend([{**head, **it} for it in items] if items else ([head] if head else []))

    payload = {"flow": graph.name or name, "count": len(rows), "data": rows,
               "run_id": run.id}
    meta = result.get("meta") or {}
    if "content_base64" in meta:
        payload["file"] = {k: meta[k] for k in
                           ("filename", "media_type", "content_base64") if k in meta}
    return payload
