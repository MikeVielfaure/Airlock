"""
pipeline_engine.py
──────────────────
The one-shot pipeline, extracted from the /api/pipeline route so it can be
reused verbatim by the flow-run endpoint (/api/flows/{id}/run).

`run_pipeline_core` takes already-loaded inputs (raw file bytes, a FileConfig,
optional TCO bytes, optional computed list) and returns a structured result
object that the HTTP layer serialises into a PipelineResponse. It contains no
FastAPI types and no I/O beyond what the services already do — which is exactly
why it can also feed the persistence layer (a flow run stores this result).

Design note: this is a straight extraction. The logic mirrors the original
inline route stage-for-stage (structure → strict_header → tco → validation →
export) so behaviour is unchanged and the existing pipeline tests still pass.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from app.models import FieldConfig, FileConfig
from app.services.config_service import ConfigService
from app.services.file_service import FileService
from app.services.process_service import ProcessService
from app.services.tco_service import TcoService


# ── the engine's return value (framework-free) ────────────────────────
@dataclass
class EngineResult:
    ok: bool
    stage: str
    error: Optional[str] = None
    # structure
    structure: Optional[dict] = None
    matched_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    identifier_field: Optional[str] = None
    # results
    stats: Optional[dict] = None
    compute_errors: dict[str, str] = field(default_factory=dict)
    tco_uncovered: dict[str, list[dict]] = field(default_factory=dict)
    report: list[dict] = field(default_factory=list)      # grouped-by-id error rows
    warnings: list[str] = field(default_factory=list)
    export: Optional[dict] = None                          # {format, filename, encoding, delimiter, rows_exported, content_base64}
    # extras used by the persistence layer (not serialised to the client)
    report_df: Optional[pd.DataFrame] = None              # full flat report, for storage
    export_bytes: Optional[bytes] = None                  # decoded export payload, for storage


def _grouped_error_report(report_df: pd.DataFrame) -> list[dict]:
    """Group flagged cells by row id, keeping only error-class statuses.
    Returns plain dicts (the route wraps them into PipelineErrorRow)."""
    if report_df is None or len(report_df) == 0:
        return []
    err = report_df[report_df["statut"].isin(["ERROR", "MAPPING_KO", "NO_TCO"])]
    if len(err) == 0:
        return []
    groups: dict[object, list[dict]] = {}
    order: list[object] = []
    for _, r in err.iterrows():
        key = r["id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        msg = str(r["resultat"])
        groups[key].append({
            "column": str(r["colonne"]),
            "value": str(r["valeur_finale"]),
            "status": str(r["statut"]),
            "message": "" if msg == "OK" else msg,
        })
    return [{"id": k, "errors": groups[k]} for k in order]


class PipelineEngine:
    def __init__(
        self,
        files: FileService | None = None,
        process: ProcessService | None = None,
        config: ConfigService | None = None,
        tco: TcoService | None = None,
    ):
        self._files = files or FileService()
        self._process = process or ProcessService()
        self._config = config or ConfigService()
        self._tco = tco or TcoService()

    # helpers duplicated from main (kept local so the engine is standalone) ──
    @staticmethod
    def _display_columns(visible_cols, fields, df_post) -> list[str]:
        # See main.py's twin of this function: `col` is the field's key, not
        # necessarily the file's actual column name — a field resolved via
        # `name` (not the key, not a rename) must still show up here.
        out, seen = [], set()
        for col in visible_cols:
            fc = fields.get(col)
            wants_rename = bool(fc and fc.mapping and getattr(fc, "rename_output", True) and fc.mapping != col)
            found_name = next((n for n in (fc.name or []) if n in df_post.columns), None) if fc else None
            if col in df_post.columns:
                final = col
            elif wants_rename and fc.mapping in df_post.columns:
                final = fc.mapping
            elif found_name:
                final = found_name
            else:
                continue
            if final not in seen:
                out.append(final)
                seen.add(final)
        return out

    def run(
        self,
        raw: bytes | None,
        fc: FileConfig,
        tco_bytes: bytes | None = None,
        computed: list[tuple[str, str]] | None = None,
        export_filename: str = "export",
        apply_filters=None,
        source_df: pd.DataFrame | None = None,
    ) -> EngineResult:
        """
        Execute the full pipeline. `apply_filters(df, fmap)` is injected so the
        engine reuses main._apply_filters (AND/OR grouping) without importing it.

        `source_df`, when given, skips the CSV/XLSX parsing below entirely — a
        table's rows are already a clean, structured frame, never a file with
        an encoding or a sheet to guess. `raw` is then ignored. Everything
        after structure (field matching, TCO, computed, export) is identical
        either way — a fixed table is just another way rows arrive, not a
        different pipeline.
        """
        if apply_filters is None:
            apply_filters = lambda df, fmap: df  # noqa: E731

        # ── 1. STRUCTURE ──────────────────────────────────────────
        ftype = (fc.type or "CSV").upper()
        sheets: list[str] = []
        sheet_used: str | None = None
        tables_found = 0
        try:
            if source_df is not None:
                df = source_df
            elif ftype == "CSV":
                delim = None if not fc.delimiter else fc.delimiter
                df, _enc, _delim = self._files.load_csv_raw(raw, fc.encoding or "AUTO", delim)
            else:
                sheets = self._files.list_sheets(raw)
                if fc.sheet:
                    if fc.sheet in sheets:
                        target: object = fc.sheet
                    elif fc.sheet.strip().lstrip("-").isdigit() and int(fc.sheet) < len(sheets):
                        target = int(fc.sheet)
                    else:
                        return EngineResult(ok=False, stage="structure",
                            error=f"Onglet « {fc.sheet} » introuvable. Onglets disponibles : {', '.join(sheets)}.")
                else:
                    target = 0
                if fc.table_marker:
                    chunks = self._files.split_tables(raw, target, fc.table_marker)
                    tables_found = len(chunks)
                    if tables_found == 0:
                        return EngineResult(ok=False, stage="structure",
                            error=f"Aucun tableau trouvé avec le marqueur « {fc.table_marker} ».")
                    if fc.table_index < 0 or fc.table_index >= tables_found:
                        return EngineResult(ok=False, stage="structure",
                            error=f"Tableau n°{fc.table_index + 1} demandé, mais {tables_found} trouvé(s).")
                    df = self._files.extract_table(raw, target, fc.table_marker, fc.table_index, fc.table_header_mode)
                else:
                    df = self._files.load_xlsx_raw(raw, sheet=target)
                sheet_used = sheets[target] if isinstance(target, int) and sheets else (target if isinstance(target, str) else None)
            work = self._process.apply_header_config(df, fc.header) if fc.header else df
        except Exception as e:  # noqa: BLE001
            return EngineResult(ok=False, stage="structure", error=f"Structure : {e}")

        cols = list(work.columns)
        structure = dict(file_type=ftype, sheet=sheet_used, sheets=sheets,
                         tables_found=tables_found, columns=cols, rows=int(len(work)))

        # ── 2. MATCH + HEADER RULES (strict_header / min_header) ──
        match = self._config.match_fields_to_columns(fc, cols)
        matched: dict[str, FieldConfig] = match["matched"]
        missing = [(f.mapping or (f.name[0] if f.name else "?")) for f in match["unmatched"]]
        extra = list(match["unused"])

        if fc.strict_header and (missing or extra):
            parts = []
            if missing:
                parts.append(f"colonnes attendues absentes : {', '.join(map(str, missing))}")
            if extra:
                parts.append(f"colonnes du fichier non déclarées : {', '.join(map(str, extra))}")
            return EngineResult(ok=False, stage="strict_header",
                error="En-tête strict : " + " ; ".join(parts) + ".",
                structure=structure, matched_columns=list(matched.keys()),
                missing_columns=missing, extra_columns=extra)

        if getattr(fc, "min_header", False) and missing:
            # Extra columns are the whole point of this mode: never blocking.
            return EngineResult(ok=False, stage="min_header",
                error="En-tête minimal : colonnes attendues absentes : "
                      + ", ".join(map(str, missing)) + ".",
                structure=structure, matched_columns=list(matched.keys()),
                missing_columns=missing, extra_columns=extra)

        fields: dict[str, FieldConfig] = {
            col: f.model_copy(update={"name": [col]}) for col, f in matched.items()
        }
        visible = list(fields.keys())
        id_fields = [col for col, f in fields.items() if f.identifiant]
        identifier_field = " | ".join(id_fields) if id_fields else None

        # ── 3. TCO ────────────────────────────────────────────────
        needs_tco = any(getattr(f, "tco_mapping", None) or getattr(f, "tco_replace", False) for f in fields.values())
        tco_df = None
        if tco_bytes:
            try:
                tco_df = self._tco.load_tco(tco_bytes)
            except Exception as e:  # noqa: BLE001
                return EngineResult(ok=False, stage="tco", error=f"TCO : {e}",
                                    structure=structure, matched_columns=visible)
        if needs_tco and tco_df is None:
            return EngineResult(ok=False, stage="tco",
                error="La config utilise une correspondance TCO mais aucun TCO valide n'a été fourni.",
                structure=structure, matched_columns=visible)

        # ── 4. VALIDATION ─────────────────────────────────────────
        try:
            result = self._process.run_pipeline(
                df_edited=work, visible_cols=visible, field_configs=fields,
                tco_df=tco_df, identifier_fields=id_fields,
                computed=computed or [], report_flagged_only=True, variables=fc.variables,
            )
        except Exception as e:  # noqa: BLE001
            return EngineResult(ok=False, stage="validation", error=f"Validation : {e}",
                                structure=structure, matched_columns=visible)

        df_post = result["df"]
        if df_post.columns.duplicated().any():
            df_post = df_post.loc[:, ~df_post.columns.duplicated()]
        report_df = result["report"]
        compute_errors = result["compute_errors"]
        stats = result["stats"]
        computed_names = result["computed_names"]

        statuses = report_df["statut"].tolist() if len(report_df) else []
        has_blocking = any(s in ("ERROR", "MAPPING_KO") for s in statuses) or bool(compute_errors)
        report = _grouped_error_report(report_df)

        common = dict(structure=structure, matched_columns=visible, missing_columns=missing,
                      extra_columns=extra, identifier_field=identifier_field, stats=stats,
                      compute_errors=compute_errors, tco_uncovered=result.get("tco_uncovered", {}),
                      report=report, warnings=result.get("warnings", []), report_df=report_df)

        if has_blocking:
            stage = "compute" if (compute_errors and not any(s in ("ERROR", "MAPPING_KO") for s in statuses)) else "validation"
            return EngineResult(ok=False, stage=stage,
                                error="Des erreurs ont été détectées — aucun export produit.", **common)

        # ── 5. EXPORT ─────────────────────────────────────────────
        display_cols = self._display_columns(visible, fields, df_post)
        display_cols += [c for c in computed_names if c not in display_cols]
        out_df = df_post[display_cols] if display_cols else df_post
        out_df = apply_filters(out_df, fc.filters or {})

        safe = "".join(ch for ch in export_filename if ch.isalnum() or ch in (" ", "-", "_")).strip() or "export"
        if ftype == "XLSX":
            buf = io.BytesIO(); out_df.to_excel(buf, index=False, na_rep=""); data = buf.getvalue()
            export = dict(format="xlsx", filename=f"{safe}.xlsx", encoding=None,
                          delimiter=None, rows_exported=int(len(out_df)),
                          content_base64=base64.b64encode(data).decode("ascii"))
        else:
            delim_out = fc.delimiter or ";"
            enc_out = fc.encoding if (fc.encoding and fc.encoding != "AUTO") else "utf-8"
            content = out_df.to_csv(index=False, sep=delim_out, na_rep="")
            try:
                data = content.encode(enc_out, errors="replace")
            except LookupError:
                enc_out = "utf-8"; data = content.encode("utf-8", errors="replace")
            export = dict(format="csv", filename=f"{safe}.csv", encoding=enc_out,
                          delimiter=delim_out, rows_exported=int(len(out_df)),
                          content_base64=base64.b64encode(data).decode("ascii"))

        return EngineResult(ok=True, stage="done", export=export, export_bytes=data, **common)
