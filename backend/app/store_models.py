"""
store_models.py
───────────────
API request/response shapes for the persistence layer (artefacts, flows, runs).
Kept in a separate module from models.py so the storage feature is self-contained
and the original data contract stays untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── artefacts ─────────────────────────────────────────────────────────
class ArtefactCreate(BaseModel):
    name: str
    description: str = ""
    note: str = ""
    environment: str = ""                      # which environment owns it ("" = default)
    # Deriving rather than starting from scratch: the ancestor is untouched (a
    # different name is a different artefact), and the link is recorded.
    derived_from: str = ""
    derived_from_version: Optional[int] = None
    # exactly one body form depending on kind:
    body: Optional[Dict[str, Any]] = None      # generic (config dict, {csv:...})
    yaml: Optional[str] = None                 # config: raw YAML (parsed + revalidated)
    computed: Optional[List[Dict[str, str]]] = None   # computed: [{name, expression}]
    # computed: [{name, expression}] where expression is a DuckDB query, not
    # a `[Col]` formula — same artefact, same library, a second engine
    # underneath for the one thing `computed` structurally cannot do.
    sql_computed: Optional[List[Dict[str, str]]] = None
    # [{column, expression}] — how a column should look, not what it holds.
    style_rules: Optional[List[Dict[str, str]]] = None
    csv: Optional[str] = None                  # tco: raw CSV text
    # tco: {TYPE: {dataset_id, query}} — restricts what TARGET_LABEL may be
    # when completing a correspondence of that type to values a DuckDB query
    # against the referenced dataset actually returns, instead of free text.
    target_sources: Optional[Dict[str, Dict[str, str]]] = None


class ArtefactUpdate(BaseModel):
    """Appends a new version (immutable history)."""
    note: str = ""
    body: Optional[Dict[str, Any]] = None
    yaml: Optional[str] = None
    computed: Optional[List[Dict[str, str]]] = None
    sql_computed: Optional[List[Dict[str, str]]] = None
    style_rules: Optional[List[Dict[str, str]]] = None
    csv: Optional[str] = None
    target_sources: Optional[Dict[str, Dict[str, str]]] = None


class VersionInfo(BaseModel):
    id: str
    version_no: int
    note: str = ""
    created_at: datetime


class ArtefactInfo(BaseModel):
    id: str
    environment: str = "default"
    derived_from: str = ""
    derived_from_version: Optional[int] = None
    kind: str
    name: str
    description: str = ""
    latest_version_no: int
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class ArtefactDetail(ArtefactInfo):
    versions: List[VersionInfo] = Field(default_factory=list)


class VersionBody(BaseModel):
    id: str
    artefact_id: str
    kind: str
    version_no: int
    body: Dict[str, Any]
    note: str = ""
    created_at: datetime


# ── flows ─────────────────────────────────────────────────────────────
class FlowCreate(BaseModel):
    name: str
    description: str = ""
    config_artefact_id: str
    config_version_no: Optional[int] = None            # None = track latest
    tco_artefact_id: Optional[str] = None
    tco_version_no: Optional[int] = None
    computed_artefact_id: Optional[str] = None
    computed_version_no: Optional[int] = None
    # An extra named frame, resolved fresh on every run, for the computed
    # artefact's own sql_computed blocks to join against — never the flow's
    # input, just another source available to `FROM self LEFT JOIN name`.
    source_artefact_id: Optional[str] = None
    source_version_no: Optional[int] = None
    default_export_filename: str = "export"
    # A fixed source, resolved fresh on every run — like tco/computed, not
    # pinned data: unlike a file, the table's *content* is expected to change
    # between runs even though the flow itself doesn't. None means "no fixed
    # source", so running still requires an uploaded file, same as before.
    source_dataset_id: Optional[str] = None


class FlowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config_artefact_id: Optional[str] = None
    config_version_no: Optional[int] = None
    tco_artefact_id: Optional[str] = None
    tco_version_no: Optional[int] = None
    computed_artefact_id: Optional[str] = None
    computed_version_no: Optional[int] = None
    source_artefact_id: Optional[str] = None
    source_version_no: Optional[int] = None
    default_export_filename: Optional[str] = None
    source_dataset_id: Optional[str] = None


class FlowInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    archived: bool = False
    config_artefact_id: str
    config_version_no: Optional[int] = None
    tco_artefact_id: Optional[str] = None
    tco_version_no: Optional[int] = None
    computed_artefact_id: Optional[str] = None
    computed_version_no: Optional[int] = None
    source_artefact_id: Optional[str] = None
    source_version_no: Optional[int] = None
    default_export_filename: str = "export"
    source_dataset_id: Optional[str] = None


# ── runs ──────────────────────────────────────────────────────────────
class RunInfo(BaseModel):
    id: str
    flow_id: Optional[str] = None
    flow_name: str = ""
    source_name: str = ""
    ok: bool
    stage: str
    error: Optional[str] = None
    rows_total: int = 0
    rows_error: int = 0
    rows_cleaned: int = 0
    created_at: datetime


class RunDetail(RunInfo):
    config_version_id: Optional[str] = None
    tco_version_id: Optional[str] = None
    computed_version_id: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    report: Dict[str, Any] = Field(default_factory=dict)
    has_export: bool = False
    export_name: Optional[str] = None
    export_format: Optional[str] = None
