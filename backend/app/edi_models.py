"""
edi_models.py
─────────────
Declarative description of ONE message type — the `edi_model` artefact body.

A model mirrors how EDIFACT specs are read: a header zone (the "head"), an
items loop (repeated per article), a summary zone. Each segment definition
says whether it is mandatory, how many times it may occur, and which
element.component positions map to which named fields — the same names the
flat pivot uses as column headers, and the generator reads back.

Qualified segments (DTM, NAD, QTY…) declare the qualifier's path once and a
`when` table: one variant per qualifier value, each with its own fields.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

_PATH_RE = re.compile(r"^\d+\.\d+$")


class EdiField(BaseModel):
    name: str                                   # pivot column name
    path: str                                   # "element.component", 1-based
    type: Literal["text", "number"] = "text"
    date_format: Optional[str] = None           # EDIFACT 2379 code ("102"…)
    codes: List[str] = Field(default_factory=list)  # allowed values ([] = any)

    @field_validator("path")
    @classmethod
    def _path_ok(cls, v: str) -> str:
        if not _PATH_RE.match(v):
            raise ValueError(f"path '{v}' doit être 'élément.composant' (1-based), ex. '1.2'")
        return v


class EdiVariant(BaseModel):
    label: str = ""
    required: bool = False                      # must appear if the segment appears at all
    fields: List[EdiField] = Field(default_factory=list)


class EdiSegmentDef(BaseModel):
    tag: str
    status: Literal["M", "C"] = "C"             # mandatory / conditional
    max: int = 1
    qualifier: Optional[str] = None             # path of the discriminator, e.g. "1.1"
    fields: List[EdiField] = Field(default_factory=list)      # when no qualifier
    when: Dict[str, EdiVariant] = Field(default_factory=dict)  # qualifier value -> variant

    @field_validator("tag")
    @classmethod
    def _tag_ok(cls, v: str) -> str:
        if not re.match(r"^[A-Z][A-Z0-9]{2}$", v):
            raise ValueError(f"tag '{v}' invalide (3 caractères, ex. NAD)")
        return v

    def all_fields(self) -> List[EdiField]:
        out = list(self.fields)
        for var in self.when.values():
            out.extend(var.fields)
        return out


class EdiItems(BaseModel):
    loop_start: str = "LIN"                     # the tag that opens each item
    segments: List[EdiSegmentDef] = Field(default_factory=list)


class EdiEnvelope(BaseModel):
    syntax: str = "UNOA:2"
    sender: str = ""
    recipient: str = ""


class EdiModel(BaseModel):
    name: str
    standard: Literal["EDIFACT"] = "EDIFACT"
    message_type: str                           # ORDERS, DESADV…
    directory: str = "D96A"
    envelope: EdiEnvelope = Field(default_factory=EdiEnvelope)
    header: List[EdiSegmentDef] = Field(default_factory=list)
    items: EdiItems = Field(default_factory=EdiItems)
    summary: List[EdiSegmentDef] = Field(default_factory=list)

    # ── derived helpers ────────────────────────────────────────────────
    def zone_defs(self, zone: str) -> List[EdiSegmentDef]:
        return {"header": self.header, "items": self.items.segments,
                "summary": self.summary}[zone]

    def field_names(self) -> dict:
        """{'header': [...], 'items': [...], 'summary': [...]} in model order."""
        out = {}
        for zone in ("header", "items", "summary"):
            names: List[str] = []
            for sd in self.zone_defs(zone):
                for f in sd.all_fields():
                    if f.name not in names:
                        names.append(f.name)
            out[zone] = names
        return out

    def duplicate_field_names(self) -> List[str]:
        seen, dupes = set(), []
        for zone in ("header", "items", "summary"):
            for n in self.field_names()[zone]:
                if n in seen and n not in dupes:
                    dupes.append(n)
                seen.add(n)
        return dupes


# ── YAML round-trip ────────────────────────────────────────────────────
def model_to_yaml(m: EdiModel) -> str:
    return yaml.safe_dump(m.model_dump(exclude_none=True, exclude_defaults=False),
                          sort_keys=False, allow_unicode=True)


def model_from_yaml(text: str) -> EdiModel:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML illisible : {e}")
    if not isinstance(data, dict):
        raise ValueError("Le YAML doit décrire un objet (mapping).")
    m = EdiModel(**data)
    dupes = m.duplicate_field_names()
    if dupes:
        raise ValueError(f"Noms de champs en double entre zones : {', '.join(dupes)}")
    return m
