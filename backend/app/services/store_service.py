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
                   computed: Optional[list], csv: Optional[str]) -> dict:
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
        if not isinstance(items, list):
            raise BadBody("A computed set needs `computed`: [{name, expression}].")
        clean = []
        for it in items:
            name, expr = it.get("name"), it.get("expression")
            if not name or not expr:
                continue
            err = _compute.validate_expression(expr)
            if err:
                raise BadBody(f"Expression for '{name}' is invalid: {err}")
            clean.append({"name": name, "expression": expr})
        if not clean:
            raise BadBody("No valid computed columns provided.")
        return {"computed": clean}

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
        return {"csv": str(text)}

    raise BadBody(f"Unknown artefact kind '{kind}'.")


# ── flow resolution + execution ───────────────────────────────────────
class ResolvedFlow:
    def __init__(self, fc: FileConfig, config_version_id: str,
                 tco_bytes: Optional[bytes], tco_version_id: Optional[str],
                 computed: list[tuple[str, str]], computed_version_id: Optional[str]):
        self.fc = fc
        self.config_version_id = config_version_id
        self.tco_bytes = tco_bytes
        self.tco_version_id = tco_version_id
        self.computed = computed
        self.computed_version_id = computed_version_id


def resolve_flow(s: Session, flow: Flow) -> ResolvedFlow:
    """Turn a flow's refs into concrete engine inputs, recording version ids."""
    cver = repo.resolve_ref(s, flow.config_artefact_id, flow.config_version_no)
    fc = FileConfig(**cver.body)

    tco_bytes = tco_vid = None
    if flow.tco_artefact_id:
        tver = repo.resolve_ref(s, flow.tco_artefact_id, flow.tco_version_no)
        tco_bytes = str(tver.body.get("csv", "")).encode("utf-8")
        tco_vid = tver.id

    computed: list[tuple[str, str]] = []
    comp_vid = None
    if flow.computed_artefact_id:
        pver = repo.resolve_ref(s, flow.computed_artefact_id, flow.computed_version_no)
        computed = [(c["name"], c["expression"]) for c in pver.body.get("computed", [])
                    if c.get("name") and c.get("expression")]
        comp_vid = pver.id

    return ResolvedFlow(fc, cver.id, tco_bytes, tco_vid, computed, comp_vid)


def run_flow(s: Session, flow: Flow, raw: bytes, source_name: str,
             apply_filters, engine: Optional[PipelineEngine] = None,
             export_filename: Optional[str] = None) -> tuple[Run, EngineResult]:
    """Execute a flow on a file and persist the run (frozen version ids + report)."""
    engine = engine or PipelineEngine()
    rf = resolve_flow(s, flow)
    res = engine.run(
        raw=raw, fc=rf.fc, tco_bytes=rf.tco_bytes, computed=rf.computed,
        export_filename=export_filename or flow.default_export_filename or "export",
        apply_filters=apply_filters,
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
