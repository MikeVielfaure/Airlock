"""
The pivot engine — the hub of the hub-and-spoke design.

Everything travels through one canonical shape (`edi_service` calls a single one
a "record"): ``{"head": {...}, "items": [{...}, ...]}``. Each source knows only
how to translate to and from this shape, guided by a `Mapping`. Converting A to
B is then "A → pivot → B": read with A's mapping, write with B's mapping, and
the pivot in the middle is the sole contract.

This module handles the *flat* spoke — CSV, Excel, datasets: anything that is a
table of rows. The *edi* spoke already exists in `edi_service`
(`extract_records` / `records_from_flat`), and `pivot_to_edi_records` below just
renames pivot fields to the EDI model's field names so the two meet in the
middle. Keeping both spokes speaking the same record shape is what makes the
whole thing compose.
"""
from __future__ import annotations

from typing import List

import pandas as pd

from app.mapping_models import Mapping, MappingLink
from app.services.compute_service import ComputeError, ComputeService

_compute = ComputeService()


def apply_expressions(df: pd.DataFrame, mapping: Mapping,
                      variables: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Materialise every computed link as a real column, so the rest of the engine
    reads sources and expressions the same way.

    This is what makes mapping and computing one feature instead of two: a link
    with an `expr` is evaluated here by the very same engine the computed-columns
    view uses, then treated as an ordinary column downstream. A link's expression
    sees the source columns plus any expression evaluated before it, so links may
    build on one another in declaration order.
    """
    errors: dict[str, str] = {}
    computed = mapping.computed_links()
    if not computed:
        return df, errors
    out = df.copy()
    for link in computed:
        col = _expr_column(link)
        try:
            out[col] = _compute.evaluate(out, link.expr or "", variables=variables or {})
        except ComputeError as e:
            errors[link.pivot] = str(e)
            out[col] = ""
        except Exception as e:  # noqa: BLE001 — a bad expression must not 500
            errors[link.pivot] = f"{type(e).__name__}: {e}"
            out[col] = ""
    return out, errors


def _expr_column(link: MappingLink) -> str:
    """Where a computed link's value lands. Prefixed so it cannot collide with a
    real source column of the same name."""
    return f"__expr__{link.scope}__{link.pivot}"


def _origin(link: MappingLink) -> str:
    """The column a link reads from, whether it was declared or computed."""
    return _expr_column(link) if link.is_computed else link.source

# A pivot record is intentionally a plain dict, not a class: it is half SQL
# (flat, typed rows) and half object (named fields), the "mix of the two" the
# whole feature is about, and a dict serialises to JSONB and to a DataFrame with
# no ceremony.
PivotRecord = dict


def _cell(v) -> str:
    """NaN-safe stringify — the v10 identifier trap, guarded once more."""
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v)
    return "" if s in ("nan", "NaT", "None") else s


# ── flat source → pivot ───────────────────────────────────────────────
def flat_to_pivot(df: pd.DataFrame, mapping: Mapping, *, group_by: str = "",
                  variables: dict | None = None) -> List[PivotRecord]:
    """
    Fold a flat table into canonical records. Head links read one value per
    group; item links read one value per row. Rows are grouped into documents by
    `group_by` (or a single document when unset), mirroring how a flat file with
    a repeated header collapses back into head + items.
    """
    head_links = mapping.head_links()
    item_links = mapping.item_links()
    df, _expr_errors = apply_expressions(df, mapping, variables)

    if not group_by:
        # A head column whose value is constant across the table is a natural
        # document key; failing that, the whole table is one document.
        for l in head_links:
            col = _origin(l)
            if col in df.columns and df[col].nunique(dropna=False) > 1:
                group_by = col
                break

    groups = df.groupby(group_by, sort=False) if group_by and group_by in df.columns else [(None, df)]
    records: List[PivotRecord] = []
    for _, g in groups:
        first = g.iloc[0]
        head = {}
        for l in head_links:
            col = _origin(l)
            val = _cell(first[col]) if col in g.columns else ""
            head[l.pivot] = val or (l.default or "")
        items = []
        for _, row in g.iterrows():
            it = {}
            for l in item_links:
                col = _origin(l)
                val = _cell(row[col]) if col in g.columns else ""
                it[l.pivot] = val or (l.default or "")
            if any(v for v in it.values()):
                items.append(it)
        records.append({"head": head, "items": items})
    return records


# ── pivot → flat ──────────────────────────────────────────────────────
def pivot_to_flat(records: List[PivotRecord], mapping: Mapping) -> pd.DataFrame:
    """
    Explode canonical records back to a flat table: one row per item, head values
    repeated on each of its rows — the shape the cleaning pipeline consumes. A
    document with no items still yields one row so head-only data survives.
    """
    head_links = [l for l in mapping.head_links() if not l.is_computed]
    item_links = [l for l in mapping.item_links() if not l.is_computed]
    rows: List[dict] = []
    for rec in records:
        head = rec.get("head", {})
        head_out = {l.source: _cell(head.get(l.pivot, l.default or "")) for l in head_links}
        items = rec.get("items") or [{}]
        for it in items:
            row = dict(head_out)
            for l in item_links:
                row[l.source] = _cell(it.get(l.pivot, l.default or ""))
            rows.append(row)
    # stable column order: head sources first, then item sources
    cols = [l.source for l in head_links] + [l.source for l in item_links]
    cols = list(dict.fromkeys(cols))
    out = pd.DataFrame(rows, columns=cols).astype("string").fillna("")

    # Computed links are produced *from* the flat result, so a target mapping can
    # derive a column the pivot never carried (a concatenation, a default, a
    # reformat) with the same expressions used everywhere else.
    for link in mapping.computed_links():
        try:
            out[link.source or link.pivot] = _compute.evaluate(out, link.expr or "")
        except Exception:  # noqa: BLE001
            out[link.source or link.pivot] = ""
    return out


# ── pivot ↔ EDI record bridge ─────────────────────────────────────────
def pivot_to_edi_records(records: List[PivotRecord], mapping: Mapping) -> List[PivotRecord]:
    """
    Rename a pivot record's fields to the EDI model's field names, so
    `edi_service.generate` can emit it. The mapping's `source` side holds the EDI
    field names; `pivot` holds the canonical names — here we translate canonical
    → EDI, i.e. pivot field to model field.
    """
    head_map = {l.pivot: l.source for l in mapping.head_links()}
    item_map = {l.pivot: l.source for l in mapping.item_links()}
    out: List[PivotRecord] = []
    for rec in records:
        head = {head_map.get(k, k): v for k, v in rec.get("head", {}).items() if k in head_map}
        items = [{item_map.get(k, k): v for k, v in it.items() if k in item_map}
                 for it in rec.get("items", [])]
        out.append({"head": head, "items": items})
    return out


def edi_records_to_pivot(records: List[PivotRecord], mapping: Mapping) -> List[PivotRecord]:
    """The reverse: an EDI record (fields named as in the model) → canonical
    pivot (fields named as `pivot`). Used when EDI is the *source*."""
    head_map = {l.source: l.pivot for l in mapping.head_links()}
    item_map = {l.source: l.pivot for l in mapping.item_links()}
    out: List[PivotRecord] = []
    for rec in records:
        head = {head_map.get(k, k): v for k, v in rec.get("head", {}).items() if k in head_map}
        items = [{item_map.get(k, k): v for k, v in it.items() if k in item_map}
                 for it in rec.get("items", [])]
        out.append({"head": head, "items": items})
    return out


# ── record ↔ flat dataframe (canonical, mapping-free) ─────────────────
def records_to_frame(records: List[PivotRecord]) -> pd.DataFrame:
    """
    The canonical flat view of pivot records, with no mapping: head fields
    prefixed nothing, items exploded per row, plus a `_doc` index so the grouping
    survives a round-trip. This is what the universal "turn into a pivot object"
    button produces — a dataset anyone downstream can read.
    """
    rows: List[dict] = []
    for d, rec in enumerate(records):
        head = rec.get("head", {})
        items = rec.get("items") or [{}]
        for it in items:
            row = {"_doc": d}
            row.update({k: _cell(v) for k, v in head.items()})
            row.update({k: _cell(v) for k, v in it.items()})
            rows.append(row)
    return pd.DataFrame(rows).astype("string").fillna("")


# ── constraints: the same validation engine, reached from the mapping ──
def check_rules(records: List[PivotRecord], mapping: Mapping) -> dict:
    """
    Validate what a mapping produces, using the very engine the cleaning
    pipeline uses on a file — `ProcessService.apply_field_configs`, driven by
    ordinary `FieldConfig` objects built from each link's `rules`.

    Reusing it rather than writing a second checker is the point: a regex means
    the same thing in a mapping as in a config, and a fix to one fixes both.
    Returns `{"ok": bool, "problems": [...], "checked": n}` where a problem names
    the pivot field, the offending value and the record it came from.
    """
    ruled = mapping.rule_links()
    if not ruled or not records:
        return {"ok": True, "problems": [], "checked": 0}

    from app.models import FieldConfig
    from app.services.process_service import ProcessService

    flat = records_to_frame(records)
    fields, wanted = [], []
    for link in ruled:
        if link.pivot not in flat.columns:
            continue
        rules = dict(link.rules or {})
        rules.setdefault("name", [link.pivot])
        try:
            fields.append(FieldConfig(**rules))
            wanted.append(link.pivot)
        except Exception as e:  # noqa: BLE001 — a bad rule is reported, not raised
            return {"ok": False, "checked": 0, "problems": [{
                "field": link.pivot, "code": "REGLE_INVALIDE", "row": -1, "value": "",
                "message": f"Constraint on '{link.pivot}' is unreadable: {e}"}]}

    if not fields:
        return {"ok": True, "problems": [], "checked": 0}

    _df, validation, _warn = ProcessService().apply_field_configs(flat, fields)

    problems = []
    for col in wanted:
        status = validation.get(col)
        if status is None:
            continue
        for pos, st in enumerate(status.tolist()):
            # The pipeline reports a failure as a human sentence ('regex KO — …',
            # 'type KO — …'), not a code: anything that is not OK is a failure.
            label = str(st).strip()
            if label and label.upper() != "OK":
                problems.append({
                    "field": col, "code": "CONTRAINTE", "row": pos,
                    "value": str(flat.iloc[pos].get(col, "")),
                    "message": f"'{col}' row {pos + 1}: {label}"})
    return {"ok": not problems, "problems": problems[:200], "checked": len(wanted)}
