"""
models.py
─────────
Single source of truth for the data contract.

Two families live here:
  • Domain models (FieldConfig / HeaderConfig / FileConfig) — the declarative
    validation schema. These serialize to/from YAML and drive the pipeline.
  • API models — request/response shapes for the HTTP layer.

Keeping them together keeps the contract honest: the UI reads these, the
services consume them, the YAML round-trips through them.
"""

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════
# DOMAIN — the declarative validation schema
# ══════════════════════════════════════════════════════════════════

class FieldConfig(BaseModel):
    """Rules attached to a single column."""
    name: Optional[List[str]] = None        # candidate header names
    type: str = "string"                    # string | integer | float | date | boolean
    format: Optional[str] = None            # source date format  (%d/%m/%Y)
    format_clean: Optional[str] = None      # target date format  (%Y-%m-%d)
    auto_date_format: bool = False          # detect source format automatically
    regex: Optional[str] = None
    length: Optional[int] = None
    nullable: bool = True
    on_list: Optional[List[Union[str, int]]] = None
    separator_mile: bool = False            # strip thousands separator
    separator_decimal: bool = False         # normalize decimal separator to "."
    delimiteur: Optional[str] = None        # character to strip from values
    trim: bool = True                       # strip leading/trailing whitespace
    normalize_case: Optional[str] = None    # upper | lower | title | None
    mapping: Optional[str] = None           # rename the column (output name)
    rename_output: bool = True              # apply `mapping` as the visible/export name
    tco_mapping: Optional[str] = None       # expected TARGET_LABEL in the TCO (validate)
    tco_replace: bool = False                # replace each value by its TARGET_LABEL (transform)
    identifiant: bool = False               # use as row id in the report
    # Name of the key protecting this column. Set = the column is confidential:
    # never stored in the clear, never displayed to a non-holder, and everything
    # derived from it inherits the mark.
    sensitive: Optional[str] = None
    check_type: bool = False                # assert value matches declared type


class HeaderConfig(BaseModel):
    """How to clean the file structure before applying field rules."""
    delete_empty_line_before_header: bool = False
    delete_empty_line_after_header: bool = False
    delete_all_empty_line: bool = False
    delete_unamed_column: bool = False
    auto_header: bool = False


class FileConfig(BaseModel):
    """The full declarative config, the thing that round-trips to YAML."""
    type: Optional[str] = None
    encoding: Optional[str] = None
    delimiter: str = ";"
    sheet: Optional[str] = None             # Excel sheet name/index to read
    table_marker: Optional[str] = None      # row text that separates stacked tables
    table_index: int = 0                    # which table to keep (0-based)
    table_header_mode: str = "local"        # "local" (per-table) or "global" (shared)
    strict_header: bool = False             # require the file header to match the config exactly
    min_header: bool = False                # require at least the config's columns; extra tolerated
    variables: Dict[str, str] = Field(default_factory=dict)   # named values usable in expressions
    delete_char_delimiter: Optional[bool] = False
    header: Optional[HeaderConfig] = None
    Fields: List[FieldConfig] = Field(default_factory=list)
    filters: Dict[str, str] = Field(default_factory=dict)   # saved table filters


# ══════════════════════════════════════════════════════════════════
# API — request bodies
# ══════════════════════════════════════════════════════════════════

class HeaderRequest(BaseModel):
    header: HeaderConfig


class ComputedColumn(BaseModel):
    name: str
    expression: str


class ProcessRequest(BaseModel):
    visible_cols: List[str]
    fields: Dict[str, FieldConfig]          # keyed by current column name
    identifier_field: Optional[str] = None
    computed: List[ComputedColumn] = Field(default_factory=list)
    # Cross-source SQL blocks — `expression` here holds a DuckDB query, not a
    # `[Col]` formula. Same shape as `computed` on purpose: one library, one
    # save/load path, just a different engine underneath.
    sql_computed: List[ComputedColumn] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)   # config variables -> value
    preview_limit: int = 150


class ExportRequest(BaseModel):
    type: str = "CSV"
    encoding: Optional[str] = None
    delimiter: str = ";"
    sheet: Optional[str] = None
    table_marker: Optional[str] = None
    table_index: int = 0
    table_header_mode: str = "local"
    strict_header: bool = False
    min_header: bool = False
    variables: Dict[str, str] = Field(default_factory=dict)
    header: HeaderConfig = Field(default_factory=HeaderConfig)
    fields: Dict[str, FieldConfig] = Field(default_factory=dict)
    visible_cols: List[str] = Field(default_factory=list)
    filters: Dict[str, str] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    yaml: str
    columns: Optional[List[str]] = None     # if given, match fields to columns


# ══════════════════════════════════════════════════════════════════
# API — response bodies
# ══════════════════════════════════════════════════════════════════

class TablePreview(BaseModel):
    columns: List[str]
    data: List[List[str]]                   # row-major cell values
    total_rows: int
    shown_rows: int
    index: List[int] = Field(default_factory=list)   # df index per row (stable key for edits)


class FileResponse(BaseModel):
    session_id: str
    type: str
    encoding: str
    delimiter: str
    sheet: Optional[str] = None
    sheets: List[str] = Field(default_factory=list)
    table_count: int = 0                    # tables detected by the marker (0 if none)
    preview: TablePreview
    # Set only when a session is seeded from a library artefact: the field rules
    # to pre-load into the Schema view, so the config is ready to run at once.
    seeded_fields: Optional[Dict[str, FieldConfig]] = None


class ColumnStat(BaseModel):
    errors: int
    cleans: int


class ProcessStats(BaseModel):
    total_rows: int
    rows_err: int
    rows_clean: int
    per_col: Dict[str, ColumnStat]


class ReportRow(BaseModel):
    id: Any
    colonne: str
    valeur_originale: str
    valeur_finale: str
    resultat: str
    statut: str


class ProcessResponse(BaseModel):
    columns: List[str]
    data: List[List[str]]                   # post-clean values
    status: List[List[str]]                 # per-cell status, aligned to data
    computed: List[str] = Field(default_factory=list)         # names of computed columns
    compute_errors: Dict[str, str] = Field(default_factory=dict)
    stats: ProcessStats
    report: List[ReportRow]
    tco_uncovered: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)  # col -> [{value, count}]
    warnings: List[str] = Field(default_factory=list)
    index: List[int] = Field(default_factory=list)   # df index per preview row (stable key for edits)
    # column -> key name, after propagation. The UI marks these columns and the
    # export refuses to emit them in the clear.
    sensitivity: Dict[str, str] = Field(default_factory=dict)
    # Keys this run depends on that no longer exist: the config cannot be used.
    missing_keys: List[str] = Field(default_factory=list)


class SourceInfo(BaseModel):
    """An extra frame attached to a session for cross-source SQL."""
    name: str
    columns: List[str]
    row_count: int


class AttachDatasetSource(BaseModel):
    name: str
    dataset_id: str


class RowsResponse(BaseModel):
    columns: List[str]
    data: List[List[str]]
    status: List[List[str]]
    total: int                              # rows after filtering
    total_all: int                          # rows before filtering
    offset: int
    limit: int
    index: List[int] = Field(default_factory=list)   # df index per row (stable key for edits)


class CellEdit(BaseModel):
    index: int                              # df index of the row (from preview/rows `index`)
    column: str                             # SOURCE column name (pre-rename, in work_df)
    value: str = ""


class EditCellsRequest(BaseModel):
    edits: List[CellEdit]


class EditCellsResponse(BaseModel):
    applied: int
    rejected: List[Dict[str, Any]] = Field(default_factory=list)   # [{index, column, reason}]
    edits_total: int                        # cumulative edits on this session
    stale: bool                             # a previous run exists and no longer reflects work_df


class AddRowsRequest(BaseModel):
    count: int = 1                          # how many rows to create
    copy_from: Optional[int] = None         # duplicate this row index instead of a blank one


class BlankSessionRequest(BaseModel):
    """
    Start a session with a schema but no file. Columns come either explicitly
    (a from-scratch table) or from a library artefact (a config or edi_model),
    so a config can be tried on hand-typed rows without fabricating a CSV.
    Exactly one source is used: `artefact_id` wins when set, else `columns`.
    """
    columns: List[str] = Field(default_factory=list)
    artefact_id: Optional[str] = None       # config or edi_model to seed columns + rules from
    artefact_version: Optional[int] = None  # pin a version; None = latest
    rows: int = 0                           # optional blank rows to start with


class DeleteRowsRequest(BaseModel):
    indices: List[int] = Field(default_factory=list)
    all_filtered: bool = False              # delete every row matching `filters`
    filters: Dict[str, str] = Field(default_factory=dict)
    statuses: List[str] = Field(default_factory=list)   # e.g. ["ERROR"] from the last run


class RowsMutationResponse(BaseModel):
    added: int = 0
    deleted: int = 0
    restored: int = 0
    total_rows: int                         # active rows after the change
    deleted_total: int                      # logically removed, still restorable
    added_total: int
    stale: bool
    # The stable index of each row just added, in order — lets a caller (e.g.
    # a table paste) fill their cells right after, without guessing which
    # indices `new_index()` handed out.
    new_indices: List[int] = Field(default_factory=list)


class ReorderRowRequest(BaseModel):
    index: int
    after: Optional[int] = None             # None = move to the very start


class ExpressionCheck(BaseModel):
    expression: str


class ExpressionResult(BaseModel):
    ok: bool
    error: Optional[str] = None


class TcoResponse(BaseModel):
    rows: int
    labels: List[str]


class ExportResponse(BaseModel):
    yaml: str


class MatchInfo(BaseModel):
    matched: Dict[str, FieldConfig]         # real_col -> field
    unmatched: List[FieldConfig]
    unused: List[str]


class ImportResponse(BaseModel):
    file_config: FileConfig
    match: Optional[MatchInfo] = None


class Presets(BaseModel):
    regex_presets: Dict[str, str]
    date_formats: List[str]
    field_types: List[str]
    encodings: List[str]
    delimiters: Dict[str, Optional[str]]
    case_modes: List[str]


# ── one-shot automation pipeline ──────────────────────────────────────
class PipelineStructure(BaseModel):
    file_type: str
    sheet: Optional[str] = None
    sheets: List[str] = Field(default_factory=list)
    tables_found: int = 0
    columns: List[str] = Field(default_factory=list)
    rows: int = 0


class PipelineErrorCell(BaseModel):
    column: str
    value: str
    status: str                              # ERROR / MAPPING_KO / NO_TCO
    message: str = ""


class PipelineErrorRow(BaseModel):
    id: Any                                  # identifier value, or row number
    errors: List[PipelineErrorCell]


class PipelineExport(BaseModel):
    format: str                              # csv | xlsx
    filename: str
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    rows_exported: int
    content_base64: str                      # the exported file bytes, base64


class PipelineResponse(BaseModel):
    ok: bool
    stage: str                               # last stage reached / where it failed
    error: Optional[str] = None              # human-readable error if a stage failed
    structure: Optional[PipelineStructure] = None
    matched_columns: List[str] = Field(default_factory=list)
    missing_columns: List[str] = Field(default_factory=list)   # config fields not found
    extra_columns: List[str] = Field(default_factory=list)     # file columns not in config
    identifier_field: Optional[str] = None
    stats: Optional[ProcessStats] = None
    compute_errors: Dict[str, str] = Field(default_factory=dict)
    tco_uncovered: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    report: List[PipelineErrorRow] = Field(default_factory=list)
    export: Optional[PipelineExport] = None
    warnings: List[str] = Field(default_factory=list)
