"""
The mapping artefact — the explicit, versioned bridge between the canonical
pivot form and any concrete source.

Why this exists
---------------
Until now the bridge between EDI and flat data was made by *coincidence of
names*: a column fed an EDI field only if it happened to be called the same
thing. That is implicit and fragile. A `mapping` makes the correspondence
explicit and, because every link is stated in both directions at once,
reversible: the same artefact drives source→pivot and pivot→source.

The canonical pivot form
------------------------
One record is `{"head": {field: value}, "items": [{field: value}, ...]}` — a
header with the fields shared by the whole document, and a list of line items.
It is the same shape `edi_service.extract_records` already produces, chosen so
the EDI engine needs no adapter. Every source translates to and from this one
shape, so N sources need 2N translators, never N².

A mapping links *pivot fields* to *source locations*. A source location is:
  - for `flat` sources (CSV, Excel, a dataset): a column name, and whether it
    sits on the head or repeats per item;
  - for `edi` sources: the field name of an EDI model (which itself resolves to
    a segment + element path — the model already knows that).
"""
from __future__ import annotations

from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class MappingLink(BaseModel):
    """
    One correspondence: a pivot field, where its value comes from, and what it
    must satisfy.

    A field's value has exactly one origin — either a `source` (a column name or
    an EDI model field) or an `expr` (an expression in the same mini-language the
    computed columns use). That is the whole unification: mapping a field and
    computing a field stopped being two different features, they are one field
    with two possible origins.

    `rules` carries the same per-field constraints the cleaning config already
    declares (type, regex, nullable, …), so a mapping can validate what it
    produces instead of handing the problem downstream. Nothing new is invented
    here: the expression engine and the validation engine are the existing ones,
    reached from one more place.
    """
    pivot: str                                  # canonical field name
    source: str = ""                            # column name, or EDI model field name
    expr: Optional[str] = None                  # computed instead of read
    scope: Literal["head", "item"] = "head"     # head-level or per-item
    default: Optional[str] = None               # constant when the origin is empty
    rules: Optional[dict] = None                # per-field constraints, as in a config

    @field_validator("pivot")
    @classmethod
    def _pivot_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("pivot must be non-empty")
        return v.strip()

    @model_validator(mode="after")
    def _one_origin(self) -> "MappingLink":
        has_src = bool(self.source and self.source.strip())
        has_expr = bool(self.expr and self.expr.strip())
        if has_src and has_expr:
            raise ValueError(f"'{self.pivot}': give either a source or an expression, not both")
        if not has_src and not has_expr and self.default is None:
            raise ValueError(f"'{self.pivot}': needs a source, an expression, or a default")
        if has_src:
            self.source = self.source.strip()
        return self

    @property
    def is_computed(self) -> bool:
        return bool(self.expr and self.expr.strip())


class Mapping(BaseModel):
    """
    A named, reversible correspondence between the pivot form and one source.

    `source_kind` says what the `source` side of each link denotes, and (for EDI)
    `source_ref` names the model whose fields are used. A mapping is deliberately
    one-sided — pivot ↔ one source — because a full conversion is then just two
    mappings sharing the pivot: read with one, write with the other, and the
    pivot in the middle is the only thing both sides must agree on.
    """
    name: str = "mapping"
    source_kind: Literal["flat", "edi"] = "flat"
    source_ref: Optional[str] = None            # edi_model artefact id/name, when source_kind == edi
    description: str = ""
    links: List[MappingLink] = Field(default_factory=list)

    def pivot_fields(self) -> List[str]:
        return list(dict.fromkeys(l.pivot for l in self.links))

    def duplicate_pivot_fields(self) -> List[str]:
        seen: dict[str, int] = {}
        for l in self.links:
            key = (l.scope, l.pivot)
            seen[key] = seen.get(key, 0) + 1
        return [p for (_scope, p), n in seen.items() if n > 1]

    def computed_links(self) -> List[MappingLink]:
        """Links whose value is produced by an expression rather than read."""
        return [l for l in self.links if l.is_computed]

    def rule_links(self) -> List[MappingLink]:
        """Links that declare constraints on what they produce."""
        return [l for l in self.links if l.rules]

    def head_links(self) -> List[MappingLink]:
        return [l for l in self.links if l.scope == "head"]

    def item_links(self) -> List[MappingLink]:
        return [l for l in self.links if l.scope == "item"]


def mapping_to_yaml(m: Mapping) -> str:
    return yaml.safe_dump(m.model_dump(exclude_none=True), allow_unicode=True, sort_keys=False)


def mapping_from_yaml(text: str) -> Mapping:
    data = yaml.safe_load(text) or {}
    m = Mapping(**data)
    dupes = m.duplicate_pivot_fields()
    if dupes:
        raise ValueError(f"Duplicate pivot field(s) in the same scope: {', '.join(sorted(dupes))}")
    return m
