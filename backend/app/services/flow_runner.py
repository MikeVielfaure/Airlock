"""
The flow runner — walks a graph and executes each brick in turn.

Design notes worth keeping in mind when adding a brick:

  * **One registry, one signature.** Every executor takes `(node, inputs, ctx)`
    and returns pivot records. Adding a brick type means writing one function and
    registering it — never touching the traversal. That is what keeps the graph
    engine from growing a special case per integration.

  * **Records are the contract.** Inputs arrive as a list of pivot records, and
    that is what a node hands on. A node cannot tell whether its parent read an
    API, a table or another flow, which is precisely why any brick can follow any
    other.

  * **Failures are located.** A brick that raises is reported with its node id
    and label. A ten-node flow that fails must say *where*, or debugging it means
    reading the whole thing.

  * **Nesting is not a special case.** A `graph` node runs another flow through
    this same runner, with its own parameter scope and a depth guard. A flow that
    proved useful becomes a brick, which is the whole point of making flows
    documents rather than records.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from app.flow_graph import SOURCE_TYPES, FlowGraph, FlowNode
from app.mapping_models import Mapping
from app.services import pivot_service

MAX_DEPTH = 5          # a graph calling a graph calling… must stop somewhere
MAX_ROWS = 200_000     # a runaway source must not eat the process


class FlowError(Exception):
    """A failure attributable to one node."""

    def __init__(self, node_id: str, message: str, label: str = ""):
        self.node_id = node_id
        self.label = label
        super().__init__(message)


@dataclass
class RunContext:
    """Everything a brick may need that is not in the graph itself."""
    params: Dict[str, str] = field(default_factory=dict)
    session: Any = None                  # SQLAlchemy session, for db-backed bricks
    depth: int = 0
    # Resolves a sub-graph by artefact id/name — injected so this module does not
    # depend on the repository layer.
    load_graph: Optional[Callable[[str, Optional[int]], FlowGraph]] = None
    # Resolves an artefact body (mapping, config…) by id.
    load_artefact: Optional[Callable[[str, Optional[int]], dict]] = None
    trace: List[dict] = field(default_factory=list)
    # Per-node results of the current run. A join needs its two parents kept
    # apart, which the merged `inputs` list cannot express.
    node_outputs: Dict[str, List[dict]] = field(default_factory=dict)
    environment: str = "default"
    graph_id: str = ""
    # Connection points resolved for this run (global → environment → flow →
    # brick). They substitute into node configs exactly like parameters do, so a
    # brick needs no notion of "variable" at all.
    variables: Dict[str, str] = field(default_factory=dict)
    # What each source brick produced, kept so a failed run can be replayed on
    # the very same data instead of calling the world again.
    snapshot: Dict[str, List[dict]] = field(default_factory=dict)
    capture: bool = True
    # Replay mode: "" (normal), "same_data" (sources read the snapshot),
    # "refetch" (sources call out again).
    replay_mode: str = ""
    messages: List[dict] = field(default_factory=list)
    # User-defined functions of this environment, resolved once per run and
    # handed to every expression evaluated inside it.
    _funcs: Optional[Dict[str, Any]] = None

    def functions(self) -> Dict[str, Any]:
        if self._funcs is None:
            if self.session is None:
                self._funcs = {}
            else:
                from app.function_models import load_registry
                self._funcs = load_registry(self.session, self.environment)
        return self._funcs


@dataclass
class NodeResult:
    records: List[dict]
    meta: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# Brick executors
# ══════════════════════════════════════════════════════════════════════
def _subs(ctx: "RunContext") -> Dict[str, str]:
    """Variables first, then parameters: an explicit call argument is more
    specific than any stored value, so it wins."""
    return {**ctx.variables, **ctx.params}


def _resolve(value: Any, params: Dict[str, str]) -> Any:
    """Substitute `{param}` placeholders in a config value. Only strings are
    touched; a missing parameter becomes empty rather than raising, so a flow
    with an optional filter still runs."""
    if isinstance(value, str):
        out = value
        for k, v in params.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    if isinstance(value, dict):
        return {k: _resolve(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, params) for v in value]
    return value


def _brick_inline(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Literal records written into the flow. Invaluable for testing a graph
    without wiring a real source to it."""
    cfg = _resolve(node.config, _subs(ctx))
    rows = cfg.get("rows") or []
    if not isinstance(rows, list):
        raise FlowError(node.id, "`rows` must be a list of records")
    # accept both shapes: flat dicts, or full {head, items} records
    if rows and "head" in rows[0]:
        return NodeResult(records=rows)
    return NodeResult(records=[{"head": {}, "items": rows}])


def _brick_api(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Call an HTTP endpoint and turn the answer into records.

    `path` walks into the payload (`data.orders`) because an API almost never
    returns a bare list at the top level. The call is deliberately plain: no
    retries, no pagination yet — a brick that silently retried would make a flow
    non-deterministic, and pagination needs a contract of its own.
    """
    import json
    import urllib.error
    import urllib.request

    cfg = _resolve(node.config, _subs(ctx))
    url = (cfg.get("url") or "").strip()
    if not url:
        raise FlowError(node.id, "an api node needs a `url`")
    method = (cfg.get("method") or "GET").upper()
    headers = cfg.get("headers") or {}
    body = cfg.get("body")
    timeout = float(cfg.get("timeout") or 20)

    data = None
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, method=method,
                                 headers={str(k): str(v) for k, v in headers.items()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raise FlowError(node.id, f"HTTP {e.code} calling {url}")
    except Exception as e:  # noqa: BLE001 — network failures must name the node
        raise FlowError(node.id, f"{type(e).__name__} calling {url}: {e}")

    try:
        payload = json.loads(raw)
    except ValueError:
        raise FlowError(node.id, f"{url} did not return JSON")

    for step in (cfg.get("path") or "").split("."):
        if not step:
            continue
        if isinstance(payload, dict):
            payload = payload.get(step)
        else:
            raise FlowError(node.id, f"path '{cfg.get('path')}' does not fit the payload")
    if payload is None:
        payload = []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise FlowError(node.id, "the API payload is neither a list nor an object")

    rows = [r for r in payload if isinstance(r, dict)][:MAX_ROWS]
    return NodeResult(records=[{"head": {}, "items": rows}],
                      meta={"status": status, "rows": len(rows)})


def _brick_dataset(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Read a stored table. The SQL brick, from the flow's point of view."""
    from app import repository as repo

    cfg = _resolve(node.config, _subs(ctx))
    ds_id = cfg.get("dataset_id") or ""
    name = cfg.get("name") or ""
    if ctx.session is None:
        raise FlowError(node.id, "a dataset node needs a database session")
    try:
        ds = repo.get_dataset(ctx.session, ds_id) if ds_id else \
            repo.find_dataset_by_name(ctx.session, name)
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"dataset not found: {e}")
    if ds is None:
        raise FlowError(node.id, f"no dataset named '{name}'")
    limit = int(cfg.get("limit") or MAX_ROWS)
    rows = [r.data for r in repo.read_rows(ctx.session, ds.id, offset=0, limit=limit)]
    return NodeResult(records=[{"head": {}, "items": rows}],
                      meta={"dataset": ds.name, "rows": len(rows)})


def _brick_session(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Read the current working table. Lets a flow born from a hand-made session
    replay against that same session — useful while building it, and the node to
    swap for a `dataset` or `api` source once the flow goes to production."""
    from app.session import store as _store

    cfg = _resolve(node.config, _subs(ctx))
    sid = cfg.get("session_id") or ""
    try:
        sess = _store.get(sid)
    except KeyError:
        raise FlowError(node.id, f"session '{sid}' is gone — point this source at "
                                 f"a dataset or an API to make the flow durable")
    rows = sess.active_df().astype(str).to_dict(orient="records")
    return NodeResult(records=[{"head": {}, "items": rows}], meta={"rows": len(rows)})


def _brick_mapping(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Reshape records through a mapping — the transform brick.

    Records arrive flat (items carry the values), so the mapping is applied to
    that flat view and folded back. This reuses the exact mapping engine the
    Mapping view uses: expressions, defaults and constraints behave identically
    inside a flow and outside it.
    """
    cfg = _resolve(node.config, _subs(ctx))
    mapping = _load_mapping(node, cfg, ctx)
    df = pivot_service.records_to_frame(_merge(inputs))
    if "_doc" in df.columns:
        df = df.drop(columns=["_doc"])
    records = pivot_service.flat_to_pivot(df, mapping,
                                          group_by=cfg.get("group_by") or "",
                                          variables=ctx.params)
    checks = pivot_service.check_rules(records, mapping)
    return NodeResult(records=records, meta={"checks": checks})


def _brick_compute(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Add derived columns without a full mapping — the same expression engine,
    reached from a lighter brick."""
    from app.services.compute_service import ComputeService

    cfg = _resolve(node.config, _subs(ctx))
    columns = cfg.get("columns") or {}
    if not isinstance(columns, dict):
        raise FlowError(node.id, "`columns` must be {name: expression}")
    df = pivot_service.records_to_frame(_merge(inputs))
    engine = ComputeService(extra_functions=ctx.functions())
    for name, expr in columns.items():
        try:
            df[name] = engine.evaluate(df, str(expr), variables=ctx.params)
        except Exception as e:  # noqa: BLE001 — a bad expression names its node
            raise FlowError(node.id, f"expression for '{name}': {e}")
    rows = df.drop(columns=["_doc"], errors="ignore").to_dict(orient="records")
    return NodeResult(records=[{"head": {}, "items": rows}])


def _brick_filter(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Keep the rows an expression judges truthy. Written as a brick rather than
    a mapping option because filtering is a graph-shaped decision: it changes
    what the downstream bricks see."""
    from app.services.compute_service import ComputeService

    cfg = _resolve(node.config, _subs(ctx))
    expr = (cfg.get("where") or "").strip()
    if not expr:
        raise FlowError(node.id, "a filter node needs a `where` expression")
    df = pivot_service.records_to_frame(_merge(inputs))
    try:
        keep = ComputeService(extra_functions=ctx.functions()).evaluate(
            df, expr, variables=ctx.params)
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"filter expression: {e}")
    truthy = {"1", "true", "True", "TRUE", "oui", "yes", "OK"}
    mask = [str(v).strip() in truthy for v in keep]
    kept = df[mask].drop(columns=["_doc"], errors="ignore")
    return NodeResult(records=[{"head": {}, "items": kept.to_dict(orient="records")}],
                      meta={"kept": int(len(kept)), "dropped": int(len(df) - len(kept))})


def _brick_validate(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Run a stored cleaning config over the records. Passing rows continue; the
    verdict travels in the node's meta so a later brick — or the caller — can
    decide what to do with the rejects."""
    cfg = _resolve(node.config, _subs(ctx))
    rules = cfg.get("rules") or {}
    if not rules:
        raise FlowError(node.id, "a validate node needs `rules`")
    links = [{"pivot": col, "source": col, "scope": "item", "rules": r}
             for col, r in rules.items()]
    mapping = Mapping(name=f"{node.id}-rules", links=links)
    records = _merge(inputs)
    checks = pivot_service.check_rules(records, mapping)
    if cfg.get("block") and not checks["ok"]:
        raise FlowError(node.id, f"{len(checks['problems'])} row(s) fail the rules")
    return NodeResult(records=records, meta={"checks": checks})


def _brick_graph(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Run another flow as a brick.

    The inner flow gets its own parameter scope: `params` in the config maps
    outer values (already substituted) onto inner names. Without that isolation
    two nested flows using the same parameter name would collide, and reuse would
    be a trap rather than a feature.
    """
    if ctx.depth >= MAX_DEPTH:
        raise FlowError(node.id, f"flows nested more than {MAX_DEPTH} deep")
    if ctx.load_graph is None:
        raise FlowError(node.id, "no graph loader available")
    cfg = _resolve(node.config, _subs(ctx))
    ref = cfg.get("graph_id") or cfg.get("graph") or ""
    if not ref:
        raise FlowError(node.id, "a graph node needs `graph_id`")
    try:
        sub = ctx.load_graph(ref, cfg.get("version"))
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"sub-flow not found: {e}")

    inner = RunContext(params={p.name: p.default for p in sub.params},
                       session=ctx.session, depth=ctx.depth + 1,
                       load_graph=ctx.load_graph, load_artefact=ctx.load_artefact)
    inner.params.update({str(k): str(v) for k, v in (cfg.get("params") or {}).items()})
    result = run_graph(sub, inner, seed=_merge(inputs))
    ctx.trace.extend({**t, "node": f"{node.id}/{t['node']}"} for t in inner.trace)
    return NodeResult(records=result["records"], meta={"sub_flow": sub.name})


def _brick_log(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Emit a message into the run journal and pass the data through untouched.

    Placeholders resolve like anywhere else, and `{rows}` is filled with what
    actually flowed through — so a flow can report "312 lignes traitées pour
    ACME" and that sentence appears in the operations table."""
    cfg = _resolve(node.config, _subs(ctx))
    rows = sum(len(r.get("items") or []) for r in _merge(inputs))
    text = str(cfg.get("message") or "").replace("{rows}", str(rows))
    level = str(cfg.get("level") or "info")
    ctx.messages.append({"node": node.id, "level": level, "text": text})
    return NodeResult(records=_merge(inputs), meta={"message": text, "level": level})


def _brick_response(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Terminal: hand the records back to the caller. This is what makes a flow
    callable as an API — the sink simply *is* the response body."""
    return NodeResult(records=_merge(inputs))


def _brick_dataset_write(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Terminal: land the records in a table, through the same write path the
    Base view uses, so modes and key stamping behave identically."""
    from app.services import dataset_service as ds_service
    from app import repository as repo

    if ctx.session is None:
        raise FlowError(node.id, "a dataset_write node needs a database session")
    cfg = _resolve(node.config, _subs(ctx))
    name = (cfg.get("name") or "").strip()
    if not name:
        raise FlowError(node.id, "a dataset_write node needs a `name`")
    mode = cfg.get("mode") or "replace"
    key_fields = list(cfg.get("key_fields") or [])

    records = _merge(inputs)
    df = pivot_service.records_to_frame(records).drop(columns=["_doc"], errors="ignore")
    target = repo.find_dataset_by_name(ctx.session, name)
    if target is None:
        target = repo.create_dataset(ctx.session, name=name, description="",
                                     schema=ds_service.schema_of(df, key_fields, {}))
    if mode == "replace":
        repo.delete_all_rows(ctx.session, target.id)
    payload = ds_service.rows_payload(df, key_fields=key_fields,
                                      error_rows=set(), policy="all")
    written = repo.insert_rows(ctx.session, target.id, payload)
    target.row_count = repo.count_rows(ctx.session, target.id)
    return NodeResult(records=records,
                      meta={"dataset": name, "written": written, "mode": mode})


def _brick_file(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """Terminal: produce a downloadable artefact — the brick that answers 'what
    file do I send?'. CSV today; the EDI writer is reachable the same way once a
    model is named."""
    import base64

    cfg = _resolve(node.config, _subs(ctx))
    fmt = (cfg.get("format") or "csv").lower()
    records = _merge(inputs)
    df = pivot_service.records_to_frame(records).drop(columns=["_doc"], errors="ignore")
    if fmt == "csv":
        content = df.to_csv(sep=cfg.get("sep") or ";", index=False).encode("utf-8-sig")
        media = "text/csv"
    elif fmt == "json":
        content = df.to_json(orient="records", force_ascii=False).encode()
        media = "application/json"
    else:
        raise FlowError(node.id, f"unsupported file format '{fmt}'")
    return NodeResult(records=records, meta={
        "filename": cfg.get("filename") or f"flow.{fmt}",
        "media_type": media,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "rows": int(len(df)),
    })



def _brick_aggregate(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Many rows in, fewer rows out — the operation everything row-wise could not do.

    `by` names the grouping keys and `agg` maps an output column to
    `{column, fn}`. Grouping is where the classic work actually happens (a total
    per client, a maximum per date, a count per category), so it is a brick of
    its own rather than a mode of some other brick: it changes the *shape* of
    the stream, and downstream bricks must be able to see that in the graph.
    """
    cfg = _resolve(node.config, _subs(ctx))
    by = [str(c) for c in (cfg.get("by") or [])]
    agg = cfg.get("agg") or {}
    if not isinstance(agg, dict) or not agg:
        raise FlowError(node.id, "an aggregate node needs `agg` = {output: {column, fn}}")

    df = pivot_service.records_to_frame(_merge(inputs)).drop(columns=["_doc"], errors="ignore")
    for key in by:
        if key not in df.columns:
            raise FlowError(node.id, f"grouping key '{key}' is not in the data")

    FNS = {"sum", "mean", "min", "max", "count", "first", "last", "nunique"}
    spec: Dict[str, tuple] = {}
    for out_name, rule in agg.items():
        if isinstance(rule, str):                      # shorthand: {total: "sum:montant"}
            fn, _, col = rule.partition(":")
        else:
            col, fn = rule.get("column", ""), rule.get("fn", "sum")
        fn = str(fn).lower()
        if fn not in FNS:
            raise FlowError(node.id, f"unknown aggregate '{fn}' (use {', '.join(sorted(FNS))})")
        if col and col not in df.columns:
            raise FlowError(node.id, f"column '{col}' is not in the data")
        spec[str(out_name)] = (col or (by[0] if by else df.columns[0]), fn)

    # Numeric aggregates need numbers; everything arrives as text, so coerce only
    # the columns that actually need it and leave the rest alone.
    work = df.copy()
    for col, fn in spec.values():
        if fn in {"sum", "mean", "min", "max"}:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    try:
        if by:
            grouped = work.groupby(by, dropna=False, sort=False)
            out = grouped.agg(**{k: (c, f) for k, (c, f) in spec.items()}).reset_index()
        else:
            row = {k: getattr(work[c], f)() if f != "nunique" else work[c].nunique()
                   for k, (c, f) in spec.items()}
            out = pd.DataFrame([row])
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"aggregation failed: {e}")

    out = out.astype(str).fillna("")
    return NodeResult(records=[{"head": {}, "items": out.to_dict(orient="records")}],
                      meta={"groups": int(len(out)), "from_rows": int(len(df))})


def _brick_join(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Two streams in, one out.

    Which parent is which side matters, so `left` and `right` name the feeding
    nodes explicitly rather than relying on edge order — a graph edited visually
    must not change meaning because two edges were re-drawn in a different order.
    """
    cfg = _resolve(node.config, _subs(ctx))
    left_id, right_id = cfg.get("left"), cfg.get("right")
    # YAML 1.1 reads a bare `on:` as the boolean True, so a perfectly natural
    # `on: [client]` arrives under the key True. Accept both spellings rather
    # than making every author discover this the hard way; `keys:` is the
    # unambiguous alias for anyone who prefers to avoid the trap entirely.
    raw_on = cfg.get("on")
    if raw_on is None:
        raw_on = cfg.get(True, cfg.get("keys"))
    on = [str(c) for c in (raw_on or [])]
    how = (cfg.get("how") or "left").lower()
    if how not in {"left", "inner", "outer", "right"}:
        raise FlowError(node.id, f"unknown join type '{how}'")
    if not on:
        raise FlowError(node.id, "a join node needs `on` (the shared key columns)")
    sides = ctx.node_outputs or {}
    if not left_id or not right_id:
        raise FlowError(node.id, "a join node needs `left` and `right` naming its two parents")
    if left_id not in sides or right_id not in sides:
        raise FlowError(node.id, f"'{left_id}' and '{right_id}' must both feed this node")

    ldf = pivot_service.records_to_frame(sides[left_id]).drop(columns=["_doc"], errors="ignore")
    rdf = pivot_service.records_to_frame(sides[right_id]).drop(columns=["_doc"], errors="ignore")
    for col in on:
        if col not in ldf.columns:
            raise FlowError(node.id, f"key '{col}' missing on the left side")
        if col not in rdf.columns:
            raise FlowError(node.id, f"key '{col}' missing on the right side")
    try:
        out = ldf.merge(rdf, on=on, how=how, suffixes=("", cfg.get("suffix") or "_r"))
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"join failed: {e}")
    out = out.astype(str).fillna("")
    return NodeResult(records=[{"head": {}, "items": out.to_dict(orient="records")}],
                      meta={"left_rows": int(len(ldf)), "right_rows": int(len(rdf)),
                            "rows": int(len(out)), "how": how})


def _brick_lookup(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Enrich from a reference table — the everyday 'replace a code by its label'.

    Distinct from `join` on purpose: a lookup must never change the number of
    rows. If the reference holds duplicate keys the first wins, and the node says
    so, instead of silently multiplying the stream the way a join would.
    """
    from app import repository as repo

    cfg = _resolve(node.config, _subs(ctx))
    key, into = cfg.get("key"), cfg.get("into")
    ref_key = cfg.get("ref_key") or key
    ref_value = cfg.get("ref_value")
    if not key or not into or not ref_value:
        raise FlowError(node.id, "a lookup needs `key`, `ref_value` and `into`")

    table = cfg.get("values")
    if isinstance(table, dict):
        ref = pd.DataFrame({ref_key: list(table.keys()), ref_value: list(table.values())})
    elif cfg.get("dataset"):
        if ctx.session is None:
            raise FlowError(node.id, "a dataset lookup needs a database session")
        ds = repo.find_dataset_by_name(ctx.session, cfg["dataset"])
        if ds is None:
            raise FlowError(node.id, f"no dataset named '{cfg['dataset']}'")
        ref = pd.DataFrame([r.data for r in repo.read_rows(ctx.session, ds.id, 0, MAX_ROWS)])
    else:
        raise FlowError(node.id, "a lookup needs `values` or `dataset`")

    df = pivot_service.records_to_frame(_merge(inputs)).drop(columns=["_doc"], errors="ignore")
    if key not in df.columns:
        raise FlowError(node.id, f"key '{key}' is not in the data")
    if ref.empty or ref_key not in ref.columns or ref_value not in ref.columns:
        raise FlowError(node.id, f"the reference has no '{ref_key}'/'{ref_value}'")

    dupes = int(ref[ref_key].duplicated().sum())
    ref = ref.drop_duplicates(subset=[ref_key], keep="first")
    table_map = dict(zip(ref[ref_key].astype(str), ref[ref_value].astype(str)))
    default = str(cfg.get("default", ""))
    before = len(df)
    df[into] = df[key].astype(str).map(lambda v: table_map.get(v, default))
    missed = int((df[into] == default).sum()) if default == "" else 0
    return NodeResult(records=[{"head": {}, "items": df.astype(str).to_dict(orient="records")}],
                      meta={"rows": before, "unmatched": missed,
                            **({"duplicate_keys_ignored": dupes} if dupes else {})})


def _brick_config(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Run a stored configuration over the records — exactly as pressing *Valider*
    in the interface does.

    This is the brick that makes configurations composable. Everything the
    interactive run does happens here: values are cleaned, then checked, and the
    **cleaned** data is what flows on. So chaining is just putting two of these
    in a row — an adaptation that reshapes a partner's file, then the contract
    that judges the result. The last one in the chain judges; the ones before
    prepare.

    `on_error` decides the fate of failing rows, and the default is deliberate:
    keeping them and reporting is safe, dropping them silently is how a flow
    quietly loses a tenth of its input.
    """
    from app.models import FieldConfig
    from app.services.compute_service import ComputeService
    from app.services.config_service import ConfigService
    from app.services.process_service import ProcessService

    cfg = _resolve(node.config, _subs(ctx))
    ref = cfg.get("config_id") or cfg.get("config") or cfg.get("name") or ""
    if not ref:
        raise FlowError(node.id, "a config node needs `config_id` (or a config name)")
    if ctx.load_artefact is None or ctx.session is None:
        raise FlowError(node.id, "no artefact loader available")

    # Accept a name as well as an id: a flow written by hand reads far better
    # with "contrat-partenaireA" than with a hex string.
    from app import repository as repo
    artefact_id = ref
    label = ref
    try:
        label = repo.get_artefact(ctx.session, ref).name
    except Exception:  # noqa: BLE001 — not an id, try it as a name
        found = [a for a in repo.list_artefacts(ctx.session, "config",
                                               environment=ctx.environment)
                 if a.name == ref]
        if not found:
            raise FlowError(node.id, f"no configuration named '{ref}' in "
                                     f"'{ctx.environment}'")
        artefact_id, label = found[0].id, found[0].name

    try:
        body = ctx.load_artefact(artefact_id, cfg.get("version"))
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"configuration unreadable: {e}")
    try:
        file_config = ConfigService()._from_dict(body)
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"invalid configuration: {e}")

    df = pivot_service.records_to_frame(_merge(inputs)).drop(columns=["_doc"],
                                                            errors="ignore")

    # A configuration may replace values through a correspondence table, so the
    # brick has to be able to load one — otherwise the very case this exists for
    # (a partner's labels mapped onto internal codes) cannot run.
    tco_df = None
    tco_ref = cfg.get("tco_id") or cfg.get("tco") or ""
    if tco_ref:
        import io as _io
        import pandas as _pd
        try:
            tco_body = ctx.load_artefact(tco_ref, cfg.get("tco_version"))
            csv_text = (tco_body or {}).get("csv", "")
            from app.services.tco_service import TcoService
            tco_df = TcoService().load_tco(csv_text.encode("utf-8"),
                                           delimiter=";", encoding="utf-8")
        except FlowError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FlowError(node.id, f"correspondence table unreadable: {e}")

    try:
        cleaned, validation, warnings = ProcessService().apply_field_configs(
            df, list(file_config.Fields), tco_df=tco_df, variables=_subs(ctx))
    except Exception as e:  # noqa: BLE001
        raise FlowError(node.id, f"{type(e).__name__}: {e}")

    # Derived columns declared alongside the configuration, if any.
    for name, expr in (cfg.get("computed") or {}).items():
        try:
            cleaned[name] = ComputeService(
                extra_functions=ctx.functions()).evaluate(cleaned, str(expr),
                                                          variables=_subs(ctx))
        except Exception as e:  # noqa: BLE001
            raise FlowError(node.id, f"expression for '{name}': {e}")

    # A configuration whose columns are absent checks nothing — and the cleaning
    # engine skips a missing column silently, so without this a partner's file
    # would pass a contract it does not satisfy at all. Reported, and fatal by
    # default: a contract that found nothing to verify has not verified anything.
    missing_columns: List[str] = []
    for f in file_config.Fields:
        wanted = list(f.name or [])
        if wanted and not any(w in df.columns for w in wanted):
            missing_columns.append(wanted[0])
    if missing_columns and cfg.get("require_columns", True):
        raise FlowError(
            node.id,
            f"configuration '{label}': column(s) absent from the data — "
            f"{', '.join(missing_columns)}. Adapt the file first, or set "
            f"require_columns: false if they are optional.")

    problems: List[dict] = []
    bad: set = set()
    for col, status in (validation or {}).items():
        for pos, st in enumerate(status.tolist()):
            label = str(st).strip()
            if label and label.upper() != "OK":
                bad.add(pos)
                if len(problems) < 200:
                    problems.append({"row": pos + 1, "column": col,
                                     "value": str(cleaned.iloc[pos].get(col, "")),
                                     "message": label})

    mode = str(cfg.get("on_error") or "keep").lower()
    if mode not in ("keep", "drop", "block"):
        raise FlowError(node.id, f"unknown on_error '{mode}' (keep, drop or block)")
    if bad and mode == "block":
        raise FlowError(node.id, f"{len(bad)} row(s) fail configuration '{label}'")
    dropped = 0
    if bad and mode == "drop":
        dropped = len(bad)
        cleaned = cleaned.drop(cleaned.index[sorted(bad)])

    rows = cleaned.astype(str).to_dict(orient="records")
    return NodeResult(records=[{"head": {}, "items": rows}],
                      meta={"config": label,
                            "rows": len(rows), "rows_in": int(len(df)),
                            "errors": len(bad), "dropped": dropped,
                            **({"missing_columns": missing_columns}
                               if missing_columns else {}),
                            "on_error": mode,
                            "problems": problems,
                            **({"warnings": warnings} if warnings else {})})


def _brick_http(node: FlowNode, inputs: List[dict], ctx: RunContext) -> NodeResult:
    """
    Send the result somewhere — the sink that closes the loop.

    A flow could read an API, clean and check the data, and then had nowhere to
    push it. This posts the rows to an endpoint, in one request or in batches.

    Two deliberate choices:
      * **Batches are reported, not hidden.** A partial failure on the fourth
        batch of ten means six were accepted: pretending the call is atomic
        would make that impossible to reason about, so each batch's status is
        recorded and the node fails naming the batch.
      * **No retry.** A silent retry turns a flow into something that cannot be
        reasoned about — was the duplicate our doing? Replaying is an explicit
        act, and the operations table already offers it.
    """
    import json
    import urllib.error
    import urllib.request

    cfg = _resolve(node.config, _subs(ctx))
    url = (cfg.get("url") or "").strip()
    if not url:
        raise FlowError(node.id, "an http node needs a `url`")
    method = (cfg.get("method") or "POST").upper()
    headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
    headers.setdefault("Content-Type", "application/json")
    timeout = float(cfg.get("timeout") or 30)
    field = cfg.get("field") or ""          # wrap rows: {"orders": [...]}
    batch = int(cfg.get("batch") or 0)      # 0 = one request with everything

    records = _merge(inputs)
    df = pivot_service.records_to_frame(records).drop(columns=["_doc"], errors="ignore")
    rows = df.astype(str).to_dict(orient="records")
    chunks = ([rows[i:i + batch] for i in range(0, len(rows), batch)]
              if batch > 0 else [rows])

    sent, statuses = 0, []
    for i, chunk in enumerate(chunks, start=1):
        payload = {field: chunk} if field else chunk
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                statuses.append(resp.status)
        except urllib.error.HTTPError as e:
            raise FlowError(node.id, f"HTTP {e.code} on batch {i}/{len(chunks)} "
                                     f"({sent} row(s) already accepted)")
        except Exception as e:  # noqa: BLE001
            raise FlowError(node.id, f"{type(e).__name__} on batch {i}/{len(chunks)}: {e}")
        sent += len(chunk)

    return NodeResult(records=records,
                      meta={"url": url, "sent": sent, "batches": len(chunks),
                            "statuses": statuses})


BRICKS: Dict[str, Callable[[FlowNode, List[dict], RunContext], NodeResult]] = {
    "inline": _brick_inline,
    "session": _brick_session,
    "api": _brick_api,
    "dataset": _brick_dataset,
    "mapping": _brick_mapping,
    "compute": _brick_compute,
    "filter": _brick_filter,
    "aggregate": _brick_aggregate,
    "join": _brick_join,
    "lookup": _brick_lookup,
    "validate": _brick_validate,
    "config": _brick_config,
    "graph": _brick_graph,
    "log": _brick_log,
    "response": _brick_response,
    "dataset_write": _brick_dataset_write,
    "file": _brick_file,
    "http": _brick_http,
}


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════
def _merge(inputs: List[dict]) -> List[dict]:
    """Several parents feeding one node concatenate. Deliberately simple: a join
    is a different operation and deserves its own brick rather than being an
    implicit surprise here."""
    return list(inputs)


def _load_mapping(node: FlowNode, cfg: dict, ctx: RunContext) -> Mapping:
    from app.mapping_models import mapping_from_yaml
    if cfg.get("mapping_yaml"):
        try:
            return mapping_from_yaml(cfg["mapping_yaml"])
        except ValueError as e:
            raise FlowError(node.id, f"invalid mapping: {e}")
    if cfg.get("mapping_id"):
        if ctx.load_artefact is None:
            raise FlowError(node.id, "no artefact loader available")
        try:
            body = ctx.load_artefact(cfg["mapping_id"], cfg.get("mapping_version"))
            return Mapping(**body)
        except Exception as e:  # noqa: BLE001
            raise FlowError(node.id, f"mapping not found: {e}")
    raise FlowError(node.id, "a mapping node needs `mapping_yaml` or `mapping_id`")


# ══════════════════════════════════════════════════════════════════════
# The traversal
# ══════════════════════════════════════════════════════════════════════
def run_graph(graph: FlowGraph, ctx: RunContext,
              seed: Optional[List[dict]] = None) -> dict:
    """
    Execute every node in dependency order and return the output node's result.

    `seed` feeds the source nodes of a *nested* graph: when a flow is used as a
    brick, whatever fed the brick must reach the sub-flow's entry points.
    """
    order = graph.topological_order()
    results: Dict[str, NodeResult] = {}

    for node_id in order:
        node = graph.node(node_id)
        brick = BRICKS.get(node.type)
        if brick is None:
            raise FlowError(node_id, f"unknown node type '{node.type}'", node.label)

        parents = graph.parents(node_id)
        inputs: List[dict] = []
        for p in parents:
            inputs.extend(results[p].records)
        if not parents and seed and node.type in ("mapping", "compute", "filter",
                                                  "validate", "response", "file",
                                                  "dataset_write"):
            inputs = list(seed)

        started = time.perf_counter()
        # Replaying "the same data" means the sources must not call the world
        # again: they hand back exactly what they produced the first time. Only
        # sources are snapshotted — everything downstream is pure computation
        # and recomputing it is the entire point of a replay.
        if (ctx.replay_mode == "same_data" and node.type in SOURCE_TYPES
                and node_id in ctx.snapshot):
            res = NodeResult(records=list(ctx.snapshot[node_id]),
                             meta={"replayed": True,
                                   "rows": sum(len(r.get("items") or [])
                                               for r in ctx.snapshot[node_id])})
            results[node_id] = res
            ctx.node_outputs[node_id] = res.records
            ctx.trace.append({"node": node_id, "type": node.type, "label": node.label,
                              "ms": 0.0, "records": len(res.records),
                              "rows": res.meta["rows"], "meta": res.meta})
            continue
        try:
                res = brick(node, inputs, ctx)
        except FlowError:
            raise
        except Exception as e:  # noqa: BLE001 — never lose which node failed
            raise FlowError(node_id, f"{type(e).__name__}: {e}", node.label)
        results[node_id] = res
        ctx.node_outputs[node_id] = res.records
        if ctx.capture and node.type in SOURCE_TYPES:
            ctx.snapshot[node_id] = res.records
        ctx.trace.append({
            "node": node_id, "type": node.type, "label": node.label,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "records": len(res.records),
            "rows": sum(len(r.get("items") or []) for r in res.records),
            **({"meta": res.meta} if res.meta else {}),
        })

    out_id = graph.resolve_output()
    out = results[out_id]
    return {"records": out.records, "output": out_id, "meta": out.meta,
            "trace": ctx.trace}
