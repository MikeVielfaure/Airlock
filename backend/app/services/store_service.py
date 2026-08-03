"""
store_service.py
────────────────
Bridges the persistence layer to the pipeline engine.

Two responsibilities:

  1. Normalise + validate artefact bodies at save time, so nothing invalid ever
     reaches storage. A config's YAML is parsed into a FileConfig (rejecting bad
     YAML with a clear error) and stored as the model dict; a computed set is
     checked expression-by-expression against the safe evaluator; a TCO's CSV is
     parsed once to confirm it loads.

  2. Run a flow: resolve each ref to a concrete version, rebuild the engine
     inputs from the stored bodies, execute, and persist a Run that freezes the
     resolved version ids (the reproducibility anchor).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app import repository as repo
from app.db_models import Flow, Run
from app.models import FileConfig
from app.services.compute_service import ComputeService
from app.services.config_service import ConfigService
from app.services.pipeline_engine import EngineResult, PipelineEngine

_config = ConfigService()
_compute = ComputeService()


class BadBody(Exception):
    pass


# ── body normalisation per kind (validate before storing) ─────────────
def normalise_body(kind: str, *, body: Optional[dict], yaml: Optional[str],
                   computed: Optional[list], csv: Optional[str],
                   sql_computed: Optional[list] = None,
                   style_rules: Optional[list] = None,
                   target_sources: Optional[dict] = None) -> dict:
    if kind == "config":
        if yaml is not None:
            try:
                fc = _config.from_yaml(yaml)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid config YAML: {e}")
            return fc.model_dump()
        if body is not None:
            try:
                fc = FileConfig(**body)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid config body: {e}")
            return fc.model_dump()
        raise BadBody("A config needs either `yaml` or `body`.")

    if kind == "computed":
        items = computed if computed is not None else (body or {}).get("computed")
        sql_items = sql_computed if sql_computed is not None else (body or {}).get("sql_computed")
        if not isinstance(items, list) and not isinstance(sql_items, list):
            raise BadBody("A computed set needs `computed`: [{name, expression}].")
        items = items if isinstance(items, list) else []
        clean = []
        for it in items:
            name, expr = it.get("name"), it.get("expression")
            if not name or not expr:
                continue
            err = _compute.validate_expression(expr)
            if err:
                raise BadBody(f"Expression for '{name}' is invalid: {err}")
            clean.append({"name": name, "expression": expr})
        # SQL blocks are not run through the expression validator — a DuckDB
        # query cannot be checked without real attached data, so it is only
        # ever verified by actually running it.
        clean_sql = []
        if isinstance(sql_items, list):
            for it in sql_items:
                name, expr = it.get("name"), it.get("expression")
                if name and expr:
                    clean_sql.append({"name": name, "expression": expr})
        # A style rule's `expression` may itself be a DuckDB query — not run
        # through the expression validator either, same reasoning as sql_computed.
        style_items = style_rules if style_rules is not None else (body or {}).get("style_rules")
        clean_style = []
        if isinstance(style_items, list):
            for it in style_items:
                column, expr = it.get("column"), it.get("expression")
                if column and expr:
                    clean_style.append({"column": column, "expression": expr})
        if not clean and not clean_sql and not clean_style:
            raise BadBody("No valid computed columns provided.")
        out = {"computed": clean}
        if clean_sql:
            out["sql_computed"] = clean_sql
        if clean_style:
            out["style_rules"] = clean_style
        return out

    if kind == "edi_model":
        if yaml is not None and yaml.strip():
            from app.edi_models import model_from_yaml
            try:
                m = model_from_yaml(yaml)
            except ValueError as e:
                raise BadBody(f"Invalid EDI model YAML: {e}")
            return m.model_dump()
        if body is not None:
            from app.edi_models import EdiModel
            try:
                m = EdiModel(**body)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid EDI model body: {e}")
            dupes = m.duplicate_field_names()
            if dupes:
                raise BadBody(f"Duplicate field names across zones: {', '.join(dupes)}")
            return m.model_dump()
        raise BadBody("An edi_model needs either `yaml` or `body`.")

    if kind == "mapping":
        if yaml is not None and yaml.strip():
            from app.mapping_models import mapping_from_yaml
            try:
                m = mapping_from_yaml(yaml)
            except ValueError as e:
                raise BadBody(f"Invalid mapping YAML: {e}")
            return m.model_dump(exclude_none=True)
        if body is not None:
            from app.mapping_models import Mapping
            try:
                m = Mapping(**body)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid mapping body: {e}")
            dupes = m.duplicate_pivot_fields()
            if dupes:
                raise BadBody(f"Duplicate pivot field(s) in the same scope: {', '.join(dupes)}")
            return m.model_dump(exclude_none=True)
        raise BadBody("A mapping needs either `yaml` or `body`.")

    if kind == "function":
        from app.function_models import UserFunction, function_from_yaml
        try:
            f = function_from_yaml(yaml) if (yaml and yaml.strip()) else UserFunction(**(body or {}))
        except Exception as e:  # noqa: BLE001
            raise BadBody(f"Invalid function: {e}")
        # A function that does not compile must never reach the library: every
        # expression in the environment would start failing at once.
        from app.services.compute_service import ComputeService
        probe = ComputeService(extra_functions={f.name: lambda *a: ""})
        err = probe.validate_expression(f.expr)
        if err:
            raise BadBody(f"Invalid expression in '{f.name}': {err}")
        return f.model_dump()

    if kind == "graph":
        if yaml is not None and yaml.strip():
            from app.flow_graph import graph_from_yaml
            try:
                g = graph_from_yaml(yaml)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid flow graph: {e}")
            return g.model_dump(by_alias=True)
        if body is not None:
            from app.flow_graph import FlowGraph
            try:
                g = FlowGraph(**body)
            except Exception as e:  # noqa: BLE001
                raise BadBody(f"Invalid flow graph: {e}")
            return g.model_dump(by_alias=True)
        raise BadBody("A graph needs either `yaml` or `body`.")

    if kind == "tco":
        text = csv if csv is not None else (body or {}).get("csv")
        if not text or not str(text).strip():
            raise BadBody("A TCO needs `csv` content.")
        # Validate it actually loads (raises with a clear message otherwise).
        from app.services.tco_service import TcoService
        try:
            TcoService().load_tco(str(text).encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise BadBody(f"Invalid TCO CSV: {e}")
        out = {"csv": str(text)}
        sources = target_sources if target_sources is not None else (body or {}).get("target_sources")
        if sources:
            if not isinstance(sources, dict):
                raise BadBody("`target_sources` must be a {type: {dataset_id, query}} object.")
            clean_sources = {}
            for type_, src in sources.items():
                if not isinstance(src, dict):
                    raise BadBody(f"target_sources['{type_}'] must be an object.")
                dataset_id, query = str(src.get("dataset_id", "")).strip(), str(src.get("query", "")).strip()
                if not dataset_id or not query:
                    raise BadBody(f"target_sources['{type_}'] needs both `dataset_id` and `query`.")
                clean_sources[str(type_)] = {"dataset_id": dataset_id, "query": query}
            if clean_sources:
                out["target_sources"] = clean_sources
        return out

    if kind == "source":
        # Structural validation only — a BDD externe/API recipe has no reason
        # to succeed outside the context it will actually run in (unlike a
        # TCO's CSV, there's nothing safe to execute at save time). `name` is
        # the SQL identifier a flow's sql_computed blocks join against
        # (`FROM self LEFT JOIN name`) — distinct from the artefact's own
        # library name.
        data = body or {}
        source_kind = data.get("source_kind")
        name = str(data.get("name", "")).strip()
        if not name:
            raise BadBody("A source needs a `name` (used in SQL joins).")
        if source_kind == "dataset":
            dataset_id = str(data.get("dataset_id", "")).strip()
            if not dataset_id:
                raise BadBody("A dataset source needs `dataset_id`.")
            return {"source_kind": "dataset", "name": name, "dataset_id": dataset_id}
        if source_kind == "external_db":
            connection = str(data.get("connection", "")).strip()
            query = str(data.get("query", "")).strip()
            if not connection or not query:
                raise BadBody("An external_db source needs `connection` and `query`.")
            params = data.get("params") or {}
            if not isinstance(params, dict):
                raise BadBody("`params` must be a {name: value} object.")
            out = {"source_kind": "external_db", "name": name, "connection": connection,
                   "query": query, "params": {str(k): str(v) for k, v in params.items()}}
            if data.get("schema_name"):
                out["schema_name"] = str(data["schema_name"])
            return out
        if source_kind == "api":
            connection = str(data.get("connection", "")).strip()
            if not connection:
                raise BadBody("An api source needs `connection`.")
            out = {"source_kind": "api", "name": name, "connection": connection,
                   "path": str(data.get("path", "")), "method": str(data.get("method", "GET")),
                   "response_kind": str(data.get("response_kind", "json")),
                   "data_path": str(data.get("data_path", ""))}
            if data.get("body") is not None:
                out["body"] = data["body"]
            if data.get("schema_name"):
                out["schema_name"] = str(data["schema_name"])
            return out
        if source_kind == "flow":
            # A flow used as a source is just another object whose value is
            # resolved lazily, exactly like the other three recipes — never a
            # live session kept open, always re-run fresh from its own fixed
            # input (checked at resolution time, since that fixed input can
            # change independently of this recipe).
            flow_id = str(data.get("flow_id", "")).strip()
            if not flow_id:
                raise BadBody("A flow source needs `flow_id`.")
            return {"source_kind": "flow", "name": name, "flow_id": flow_id}
        raise BadBody("A source needs `source_kind` to be one of dataset, external_db, api, flow.")

    raise BadBody(f"Unknown artefact kind '{kind}'.")


# ── flow resolution + execution ───────────────────────────────────────
class ResolvedFlow:
    def __init__(self, fc: FileConfig, config_version_id: str,
                 tco_bytes: Optional[bytes], tco_version_id: Optional[str],
                 computed: list[tuple[str, str]], computed_version_id: Optional[str],
                 sql_computed: Optional[list[tuple[str, str, str]]] = None,
                 attached: Optional[dict] = None):
        self.fc = fc
        self.config_version_id = config_version_id
        self.tco_bytes = tco_bytes
        self.tco_version_id = tco_version_id
        self.computed = computed
        self.computed_version_id = computed_version_id
        self.sql_computed = sql_computed or []
        self.attached = attached or {}


def resolve_flow(s: Session, flow: Flow, _chain: frozenset = frozenset()) -> ResolvedFlow:
    """Turn a flow's refs into concrete engine inputs, recording version ids.

    `_chain` is the set of flow ids already being resolved on the current
    call stack — a flow used as another flow's source is re-run fresh every
    time (never a cached/kept-open session), so a cycle (A's source is B,
    B's source is A) would otherwise recurse until the stack blows up. The
    check happens here, at resolution time, rather than when a flow or a
    source artefact is saved — a cycle can be introduced later by a new
    version of a *shared* source artefact without either flow being touched,
    so only a check at the moment of use is actually reliable."""
    if len(_chain) > 25:
        raise ValueError("chaîne de flux imbriqués trop profonde — vérifiez une éventuelle référence circulaire.")
    cver = repo.resolve_ref(s, flow.config_artefact_id, flow.config_version_no)
    fc = FileConfig(**cver.body)

    tco_bytes = tco_vid = None
    if flow.tco_artefact_id:
        tver = repo.resolve_ref(s, flow.tco_artefact_id, flow.tco_version_no)
        tco_bytes = str(tver.body.get("csv", "")).encode("utf-8")
        tco_vid = tver.id

    computed: list[tuple[str, str]] = []
    sql_computed: list[tuple[str, str, str]] = []
    comp_vid = None
    if flow.computed_artefact_id:
        pver = repo.resolve_ref(s, flow.computed_artefact_id, flow.computed_version_no)
        computed = [(c["name"], c["expression"]) for c in pver.body.get("computed", [])
                    if c.get("name") and c.get("expression")]
        sql_computed = [(c["name"], c["expression"], c.get("mode", "replace"))
                        for c in pver.body.get("sql_computed", [])
                        if c.get("name") and c.get("expression")]
        comp_vid = pver.id

    attached: dict = {}
    if flow.source_artefact_id:
        from app.services import source_recipe
        sver = repo.resolve_ref(s, flow.source_artefact_id, flow.source_version_no)
        source_art = repo.get_artefact(s, flow.source_artefact_id)
        scope = source_art.environment or repo.DEFAULT_ENV
        if sver.body.get("source_kind") == "flow" and sver.body.get("flow_id") in (_chain | {flow.id}):
            raise ValueError(f"référence circulaire de flux détectée sur la source « {sver.body['name']} ».")
        frame = source_recipe.build_source_frame(s, sver.body, scope, _chain=_chain | {flow.id})
        attached[sver.body["name"]] = frame

    return ResolvedFlow(fc, cver.id, tco_bytes, tco_vid, computed, comp_vid, sql_computed, attached)


def run_flow(s: Session, flow: Flow, raw: Optional[bytes], source_name: str,
             apply_filters, engine: Optional[PipelineEngine] = None,
             export_filename: Optional[str] = None,
             source_df=None, _chain: frozenset = frozenset()) -> tuple[Run, EngineResult]:
    """Execute a flow on a file, or on a pre-loaded table (`source_df`), and
    persist the run (frozen version ids + report). Exactly one of
    `raw`/`source_df` is given — the caller decides which, `raw` stays first
    for the existing (and only) call site."""
    engine = engine or PipelineEngine()
    rf = resolve_flow(s, flow, _chain=_chain)
    res = engine.run(
        raw=raw, fc=rf.fc, tco_bytes=rf.tco_bytes, computed=rf.computed,
        sql_computed=rf.sql_computed, attached=rf.attached,
        export_filename=export_filename or flow.default_export_filename or "export",
        apply_filters=apply_filters, source_df=source_df,
    )
    run = _persist_run(
        s, res, flow_id=flow.id, flow_name=flow.name, source_name=source_name,
        config_version_id=rf.config_version_id, tco_version_id=rf.tco_version_id,
        computed_version_id=rf.computed_version_id,
    )
    return run, res


def _persist_run(s: Session, res: EngineResult, *, flow_id: Optional[str], flow_name: str,
                 source_name: str, config_version_id: Optional[str],
                 tco_version_id: Optional[str], computed_version_id: Optional[str]) -> Run:
    stats = res.stats or {}
    summary = {"structure": res.structure, "stats": stats,
               "matched_columns": res.matched_columns,
               "missing_columns": res.missing_columns, "extra_columns": res.extra_columns,
               "identifier_field": res.identifier_field,
               "compute_errors": res.compute_errors, "tco_uncovered": res.tco_uncovered,
               "warnings": res.warnings}
    return repo.create_run(
        s,
        flow_id=flow_id, flow_name=flow_name, source_name=source_name,
        ok=res.ok, stage=res.stage, error=res.error,
        config_version_id=config_version_id, tco_version_id=tco_version_id,
        computed_version_id=computed_version_id,
        rows_total=int(stats.get("total_rows", 0)),
        rows_error=int(stats.get("rows_err", 0)),
        rows_cleaned=int(stats.get("rows_clean", 0)),
        summary=summary, report={"rows": res.report},
        export_b64=(res.export or {}).get("content_base64") if res.export else None,
        export_name=(res.export or {}).get("filename") if res.export else None,
        export_format=(res.export or {}).get("format") if res.export else None,
    )
