"""
edi_service.py
──────────────
The EDI engine, on top of the lexer. Pure functions over parsed segments:

  split_interchanges  UNB/UNZ + UNH/UNT structure, with the free syntax checks
                      EDIFACT carries (segment counts, paired references).
  decode_tree         plain-language view of a file (feeds the inspector UI).
  validate            semantic control against an EdiModel (mandatory segments,
                      cardinalities, qualifiers, codes, dates, numbers).
  extract_records     model-driven extraction: one record per message
                      ({head fields}, [item fields…]) — best effort even when
                      validation reports errors.
  pivot               records -> DataFrames, flat (head repeated per item) or
                      linked (heads + items tables joined on message_no).
  generate            records -> EDIFACT text (escaping, envelopes, UNT/UNZ
                      counters computed, decimal char honoured).
  convert             source model -> records -> target model. The pivot is
                      the lingua franca: N models cover N×N conversions.
  infer_model         propose a model skeleton from a sample file, named via
                      the knowledge base — the "compose a model from a file".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.edi_models import (EdiEnvelope, EdiField, EdiItems, EdiModel,
                            EdiSegmentDef, EdiVariant)
from app.services import edi_kb as kb
from app.services.edi_lexer import (Segment, Separators, get, parse, render)

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


# ══════════════════════════════════════════════════════════════════════
# Structure: interchanges & messages, plus the free syntax checks
# ══════════════════════════════════════════════════════════════════════
@dataclass
class EdiMessage:
    ref: str = ""
    mtype: str = ""
    directory: str = ""
    segments: list[Segment] = field(default_factory=list)   # UNH..UNT inclusive


@dataclass
class Interchange:
    sender: str = ""
    recipient: str = ""
    ref: str = ""
    implicit: bool = False            # no UNB in the file
    messages: list[EdiMessage] = field(default_factory=list)


def _err(code: str, message: str, *, segment_pos: int = 0, tag: str = "",
         message_no: int = 0, zone: str = "", path: str = "") -> dict:
    return {"code": code, "message": message, "segment_pos": segment_pos,
            "tag": tag, "message_no": message_no, "zone": zone, "path": path}


def split_interchanges(segments: list[Segment]) -> tuple[list[Interchange], list[dict]]:
    inters: list[Interchange] = []
    errors: list[dict] = []
    cur_i: Optional[Interchange] = None
    cur_m: Optional[EdiMessage] = None

    for seg in segments:
        t = seg.tag
        if t == "UNB":
            if cur_m is not None:
                errors.append(_err("UNT_MANQUANT", "UNB rencontré alors qu'un message est ouvert (UNT manquant).", segment_pos=seg.pos, tag=t))
                cur_m = None
            if cur_i is not None:
                errors.append(_err("UNZ_MANQUANT", "Nouvel UNB sans UNZ pour l'interchange précédent.", segment_pos=seg.pos, tag=t))
            cur_i = Interchange(sender=get(seg, "2.1"), recipient=get(seg, "3.1"), ref=get(seg, "5.1"))
            inters.append(cur_i)
        elif t == "UNZ":
            if cur_m is not None:
                errors.append(_err("UNT_MANQUANT", "UNZ rencontré alors qu'un message est ouvert (UNT manquant).", segment_pos=seg.pos, tag=t))
                cur_m = None
            if cur_i is None:
                errors.append(_err("UNZ_ORPHELIN", "UNZ sans UNB correspondant.", segment_pos=seg.pos, tag=t))
                continue
            count, ref = get(seg, "1.1"), get(seg, "2.1")
            if count and count != str(len(cur_i.messages)):
                errors.append(_err("COMPTEUR_UNZ", f"UNZ annonce {count} message(s), l'interchange en contient {len(cur_i.messages)}.", segment_pos=seg.pos, tag=t))
            if ref and cur_i.ref and ref != cur_i.ref:
                errors.append(_err("REF_INTERCHANGE", f"Référence UNZ '{ref}' ≠ référence UNB '{cur_i.ref}'.", segment_pos=seg.pos, tag=t))
            cur_i = None
        elif t == "UNH":
            if cur_m is not None:
                errors.append(_err("UNT_MANQUANT", "UNH rencontré alors qu'un message est ouvert (UNT manquant).", segment_pos=seg.pos, tag=t))
            if cur_i is None:
                cur_i = Interchange(implicit=True)
                inters.append(cur_i)
            cur_m = EdiMessage(ref=get(seg, "1.1"), mtype=get(seg, "2.1"),
                               directory=(get(seg, "2.2") + get(seg, "2.3")),
                               segments=[seg])
            cur_i.messages.append(cur_m)
        elif t == "UNT":
            if cur_m is None:
                errors.append(_err("UNT_ORPHELIN", "UNT sans UNH correspondant.", segment_pos=seg.pos, tag=t))
                continue
            cur_m.segments.append(seg)
            count, ref = get(seg, "1.1"), get(seg, "2.1")
            real = len(cur_m.segments)
            if count and count != str(real):
                errors.append(_err("COMPTEUR_UNT", f"UNT annonce {count} segment(s), le message en contient {real}.", segment_pos=seg.pos, tag=t, message_no=len(cur_i.messages)))
            if ref and cur_m.ref and ref != cur_m.ref:
                errors.append(_err("REF_MESSAGE", f"Référence UNT '{ref}' ≠ référence UNH '{cur_m.ref}'.", segment_pos=seg.pos, tag=t, message_no=len(cur_i.messages)))
            cur_m = None
        else:
            if cur_m is not None:
                cur_m.segments.append(seg)
            else:
                errors.append(_err("SEGMENT_HORS_MESSAGE", f"Segment {t} hors de tout message (entre UNB/UNH ou après UNT).", segment_pos=seg.pos, tag=t))

    if cur_m is not None:
        errors.append(_err("UNT_MANQUANT", "Fin de fichier : dernier message sans UNT.", tag="UNT"))
    if cur_i is not None and not cur_i.implicit:
        errors.append(_err("UNZ_MANQUANT", "Fin de fichier : UNZ manquant pour clore l'interchange.", tag="UNZ"))
    if not inters:
        errors.append(_err("VIDE", "Aucun message trouvé dans le fichier."))
    return inters, errors


# ══════════════════════════════════════════════════════════════════════
# Plain-language decode (inspector)
# ══════════════════════════════════════════════════════════════════════
def decode_tree(seps: Separators, inters: list[Interchange], limit: int = 400) -> dict:
    shown = 0
    out_i = []
    total = sum(len(m.segments) for i in inters for m in i.messages)
    for i in inters:
        msgs = []
        for m in i.messages:
            segs = []
            for s in m.segments:
                if shown >= limit:
                    break
                q = get(s, "1.1")
                decoded = []
                for ei, comps in enumerate(s.elements, start=1):
                    for ci, val in enumerate(comps, start=1):
                        if val == "":
                            continue
                        p = f"{ei}.{ci}"
                        decoded.append({"path": p, "value": val,
                                        "label": kb.element_label(s.tag, p)})
                segs.append({"pos": s.pos, "tag": s.tag, "raw": s.raw,
                             "label": kb.segment_label(s.tag),
                             "qualifier_label": kb.qualifier_label(s.tag, q),
                             "elements": decoded})
                shown += 1
            msgs.append({"ref": m.ref, "type": m.mtype, "directory": m.directory,
                         "segment_count": len(m.segments), "segments": segs})
        out_i.append({"sender": i.sender, "recipient": i.recipient, "ref": i.ref,
                      "implicit": i.implicit, "messages": msgs})
    return {"separators": seps.as_dict(), "interchanges": out_i,
            "total_segments": total, "truncated": shown < total}


# ══════════════════════════════════════════════════════════════════════
# Zoning: split a message body into header / item loops / summary
# ══════════════════════════════════════════════════════════════════════
def _zones(m: EdiMessage, model: EdiModel) -> tuple[list[Segment], list[list[Segment]], list[Segment]]:
    body = m.segments[1:-1] if len(m.segments) >= 2 and m.segments[-1].tag == "UNT" else m.segments[1:]
    loop_tag = model.items.loop_start
    summary_tags = {sd.tag for sd in model.summary}
    first_loop = next((idx for idx, s in enumerate(body) if s.tag == loop_tag), None)
    if first_loop is None:
        # no items: everything before the first summary tag is header
        cut = next((idx for idx, s in enumerate(body) if s.tag in summary_tags), len(body))
        return body[:cut], [], body[cut:]
    header = body[:first_loop]
    loops: list[list[Segment]] = []
    i, n = first_loop, len(body)
    summary_start = n
    while i < n:
        s = body[i]
        if s.tag == "UNS" or (s.tag in summary_tags and s.tag != loop_tag and not loops):
            summary_start = i
            break
        if s.tag == loop_tag:
            loops.append([s])
            i += 1
            while i < n and body[i].tag != loop_tag:
                if body[i].tag == "UNS":
                    break
                loops[-1].append(body[i])
                i += 1
            if i < n and body[i].tag == "UNS":
                summary_start = i
                break
            continue
        summary_start = i
        break
    # peel trailing summary-tag segments accidentally captured by the last loop
    if loops and summary_start == n:
        tail = loops[-1]
        while len(tail) > 1 and tail[-1].tag in summary_tags:
            summary_start -= 1
            tail.pop()
    return header, loops, body[summary_start:]


# ══════════════════════════════════════════════════════════════════════
# Semantic validation against a model
# ══════════════════════════════════════════════════════════════════════
def _check_field(f: EdiField, seg: Segment, seps: Separators, *, msg_no: int,
                 zone: str, errors: list[dict]) -> None:
    val = get(seg, f.path)
    if val == "":
        return
    if f.codes and val not in f.codes:
        errors.append(_err("CODE_INTERDIT", f"'{val}' n'est pas dans la liste autorisée pour {f.name} ({', '.join(f.codes)}).",
                           segment_pos=seg.pos, tag=seg.tag, message_no=msg_no, zone=zone, path=f.path))
    if f.type == "number":
        norm = val.replace(seps.decimal, ".")
        if not _NUM_RE.match(norm):
            errors.append(_err("NOMBRE_INVALIDE", f"'{val}' n'est pas un nombre valide pour {f.name}.",
                               segment_pos=seg.pos, tag=seg.tag, message_no=msg_no, zone=zone, path=f.path))
    if f.date_format:
        fmt = kb.DATE_FORMATS.get(f.date_format)
        if fmt and not re.match(fmt[1], val):
            errors.append(_err("DATE_INVALIDE", f"'{val}' ne respecte pas le format {f.date_format} ({fmt[0]}) pour {f.name}.",
                               segment_pos=seg.pos, tag=seg.tag, message_no=msg_no, zone=zone, path=f.path))


def _check_zone(defs: list[EdiSegmentDef], segs: list[Segment], seps: Separators, *,
                msg_no: int, zone: str, errors: list[dict]) -> None:
    by_tag: dict[str, list[Segment]] = {}
    for s in segs:
        by_tag.setdefault(s.tag, []).append(s)
    known = {d.tag for d in defs}
    for s in segs:
        if s.tag not in known:
            errors.append(_err("SEGMENT_INATTENDU", f"Segment {s.tag} non prévu par le modèle dans la zone {zone}.",
                               segment_pos=s.pos, tag=s.tag, message_no=msg_no, zone=zone))
    for d in defs:
        occ = by_tag.get(d.tag, [])
        if d.status == "M" and not occ:
            errors.append(_err("SEGMENT_MANQUANT", f"Segment obligatoire {d.tag} absent ({zone}).",
                               tag=d.tag, message_no=msg_no, zone=zone))
            continue
        if len(occ) > d.max:
            errors.append(_err("TROP_D_OCCURRENCES", f"{d.tag} présent {len(occ)} fois (max {d.max}) dans la zone {zone}.",
                               segment_pos=occ[d.max].pos, tag=d.tag, message_no=msg_no, zone=zone))
        if d.qualifier:
            seen: set[str] = set()
            for s in occ:
                q = get(s, d.qualifier)
                seen.add(q)
                var = d.when.get(q)
                if var is None:
                    if d.when:
                        errors.append(_err("QUALIFIANT_INCONNU", f"{d.tag} avec qualifiant '{q}' non prévu par le modèle.",
                                           segment_pos=s.pos, tag=d.tag, message_no=msg_no, zone=zone, path=d.qualifier))
                    continue
                for f in var.fields:
                    _check_field(f, s, seps, msg_no=msg_no, zone=zone, errors=errors)
            for q, var in d.when.items():
                if var.required and occ and q not in seen:
                    errors.append(_err("VARIANTE_MANQUANTE", f"{d.tag} qualifiant '{q}' ({var.label or 'variante requise'}) absent.",
                                       tag=d.tag, message_no=msg_no, zone=zone, path=d.qualifier))
        else:
            for s in occ:
                for f in d.fields:
                    _check_field(f, s, seps, msg_no=msg_no, zone=zone, errors=errors)


def validate(seps: Separators, inters: list[Interchange], model: EdiModel) -> tuple[list[dict], dict]:
    errors: list[dict] = []
    n_msg = n_items = 0
    for inter in inters:
        for m in inter.messages:
            n_msg += 1
            if model.message_type and m.mtype and m.mtype != model.message_type:
                errors.append(_err("TYPE_MESSAGE", f"Le message {n_msg} est un {m.mtype}, le modèle attend {model.message_type}.",
                                   message_no=n_msg, tag="UNH", segment_pos=m.segments[0].pos if m.segments else 0))
            header, loops, summary = _zones(m, model)
            n_items += len(loops)
            _check_zone(model.header, header, seps, msg_no=n_msg, zone="header", errors=errors)
            for li, loop in enumerate(loops, start=1):
                _check_zone(model.items.segments, loop, seps, msg_no=n_msg, zone=f"item {li}", errors=errors)
            _check_zone(model.summary, summary, seps, msg_no=n_msg, zone="summary", errors=errors)
    stats = {"messages": n_msg, "items": n_items, "errors": len(errors)}
    return errors, stats


# ══════════════════════════════════════════════════════════════════════
# Extraction & pivot
# ══════════════════════════════════════════════════════════════════════
def _extract_zone(defs: list[EdiSegmentDef], segs: list[Segment]) -> dict:
    rec: dict[str, str] = {}
    by_tag: dict[str, list[Segment]] = {}
    for s in segs:
        by_tag.setdefault(s.tag, []).append(s)
    for d in defs:
        occ = by_tag.get(d.tag, [])
        if d.qualifier:
            for s in occ:
                var = d.when.get(get(s, d.qualifier))
                if var is None:
                    continue
                for f in var.fields:
                    rec.setdefault(f.name, get(s, f.path))
        else:
            for s in occ[:1] if d.max == 1 else occ:
                for f in d.fields:
                    rec.setdefault(f.name, get(s, f.path))
    return rec


def extract_records(inters: list[Interchange], model: EdiModel) -> list[dict]:
    """One record per message: {'head': {...}, 'items': [{...}, …]}."""
    records = []
    for inter in inters:
        for m in inter.messages:
            header, loops, summary = _zones(m, model)
            head = _extract_zone(model.header, header)
            head.update(_extract_zone(model.summary, summary))
            items = [_extract_zone(model.items.segments, loop) for loop in loops]
            records.append({"head": head, "items": items,
                            "message_ref": m.ref, "message_type": m.mtype})
    return records


def pivot(records: list[dict], model: EdiModel, mode: str = "flat") -> dict:
    names = model.field_names()
    head_cols = names["header"] + [n for n in names["summary"] if n not in names["header"]]
    item_cols = names["items"]
    heads_rows, items_rows, flat_rows = [], [], []
    for mi, rec in enumerate(records, start=1):
        head = {c: rec["head"].get(c, "") for c in head_cols}
        heads_rows.append({"message_no": mi, **head})
        its = rec["items"] or [dict()]
        for ii, it in enumerate(its, start=1):
            row_it = {c: it.get(c, "") for c in item_cols}
            items_rows.append({"message_no": mi, "item_no": ii, **row_it})
            flat_rows.append({"message_no": mi, "item_no": ii, **head, **row_it})
    heads_df = pd.DataFrame(heads_rows, columns=["message_no"] + head_cols)
    items_df = pd.DataFrame(items_rows, columns=["message_no", "item_no"] + item_cols)
    flat_df = pd.DataFrame(flat_rows, columns=["message_no", "item_no"] + head_cols + item_cols)
    if mode == "linked":
        return {"mode": "linked", "heads": heads_df.astype(str), "items": items_df.astype(str)}
    return {"mode": "flat", "flat": flat_df.astype(str)}


# ══════════════════════════════════════════════════════════════════════
# Generation (records -> EDIFACT text)
# ══════════════════════════════════════════════════════════════════════
def _emit_segment(d: EdiSegmentDef, values: dict, mapping: dict, seps: Separators) -> list[tuple[str, list[list[str]]]]:
    def val_of(f: EdiField) -> str:
        v = str(values.get(mapping.get(f.name, f.name), "") or "")
        if f.type == "number" and v:
            v = v.replace(",", ".").replace(".", seps.decimal)
        return v

    def build(pairs: list[tuple[str, str]]) -> Optional[list[list[str]]]:
        filled = [(p, v) for p, v in pairs if v != ""]
        if not filled:
            return None
        max_e = max(int(p.split(".")[0]) for p, _ in filled)
        elements: list[list[str]] = [[""] for _ in range(max_e)]
        for p, v in filled:
            e, c = (int(x) for x in p.split("."))
            comps = elements[e - 1]
            while len(comps) < c:
                comps.append("")
            comps[c - 1] = v
        return elements

    out = []
    if d.qualifier:
        for q, var in d.when.items():
            pairs = [(f.path, val_of(f)) for f in var.fields]
            has_data = any(v for _, v in pairs)
            # `status: M` means "at least one variant must appear", NOT "every
            # variant must". A variant is emitted when it carries data, when it
            # is explicitly required, or when it is a pure marker (no fields at
            # all) on a mandatory segment — the UNS+S case.
            marker = d.status == "M" and not var.fields
            if not has_data and not var.required and not marker:
                continue
            elements = build([(d.qualifier, q)] + pairs) or build([(d.qualifier, q)])
            if elements:
                out.append((d.tag, elements))
    else:
        pairs = [(f.path, val_of(f)) for f in d.fields]
        elements = build(pairs)
        if elements:
            out.append((d.tag, elements))
        elif d.status == "M" and not d.fields:
            out.append((d.tag, []))
    return out


def generate(model: EdiModel, records: list[dict], *, sender: str = "",
             recipient: str = "", interchange_ref: str = "",
             mapping: Optional[dict] = None, una: bool = True) -> str:
    seps = Separators()
    mapping = mapping or {}
    now = datetime.now(timezone.utc)
    env = model.envelope or EdiEnvelope()
    sender = sender or env.sender
    recipient = recipient or env.recipient
    ref = interchange_ref or ("IC" + now.strftime("%y%m%d%H%M%S"))
    syntax = (env.syntax or "UNOA:2").split(":")
    version, release = (model.directory[:1] or "D"), (model.directory[1:] or "96A")

    out: list[tuple[str, list[list[str]]]] = []
    # sender/recipient are structured ("GLN:codelist"): split them into
    # components instead of letting the writer escape the separator.
    out.append(("UNB", [[syntax[0], syntax[1] if len(syntax) > 1 else "2"],
                        sender.split(seps.component), recipient.split(seps.component),
                        [now.strftime("%y%m%d"), now.strftime("%H%M")], [ref]]))
    for mi, rec in enumerate(records, start=1):
        mref = rec.get("message_ref") or f"M{mi:06d}"
        body: list[tuple[str, list[list[str]]]] = []
        body.append(("UNH", [[mref], [model.message_type, version, release, "UN"]]))
        head = rec.get("head", {})
        for d in model.header:
            body.extend(_emit_segment(d, head, mapping, seps))
        items = [it for it in rec.get("items", []) if any(str(v).strip() for v in it.values())]
        for it in items:
            for d in model.items.segments:
                body.extend(_emit_segment(d, it, mapping, seps))
        for d in model.summary:
            vals = dict(head)
            if d.tag == "CNT" and "2" in d.when:
                for f in d.when["2"].fields:
                    key = mapping.get(f.name, f.name)
                    if not str(vals.get(key, "") or "").strip():
                        vals[key] = str(len(items))
            body.extend(_emit_segment(d, vals, mapping, seps))
        body.append(("UNT", [[str(len(body) + 1)], [mref]]))
        out.extend(body)
    out.append(("UNZ", [[str(len(records))], [ref]]))
    return render(out, seps, una=una)


def records_from_flat(df: pd.DataFrame, model: EdiModel, group_by: str = "") -> list[dict]:
    names = model.field_names()
    head_cols = [c for c in df.columns if c in set(names["header"] + names["summary"])]
    item_cols = [c for c in df.columns if c in set(names["items"])]
    df = df.fillna("").astype(str)
    if not group_by:
        group_by = "message_no" if "message_no" in df.columns else ""
    groups = df.groupby(group_by, sort=False) if group_by else [(None, df)]
    records = []
    for _, g in groups:
        head = {c: str(g.iloc[0][c]).strip() for c in head_cols}
        items = []
        for _, row in g.iterrows():
            it = {c: str(row[c]).strip() for c in item_cols}
            if any(v for v in it.values()):
                items.append(it)
        records.append({"head": head, "items": items})
    return records


def convert(inters: list[Interchange], source: EdiModel, target: EdiModel,
            mapping: Optional[dict] = None, **env) -> str:
    records = extract_records(inters, source)
    return generate(target, records, mapping=mapping, **env)


# ══════════════════════════════════════════════════════════════════════
# Model inference from a sample file
# ══════════════════════════════════════════════════════════════════════
def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "_", t).strip("_").lower()
    return t or "champ"


def _field_name(tag: str, qual: str, path: str) -> str:
    stem = kb.FIELD_STEMS.get((tag, qual)) or kb.FIELD_STEMS.get((tag, ""))
    if stem:
        if tag == "NAD" and path == "2.1":
            return f"{stem}_id"
        if tag in ("DTM", "QTY", "PRI", "MOA", "RFF", "CNT") and path == "1.2":
            return stem
        if path == "1.3":
            return f"{stem}_unite"
        lbl = kb.element_label(tag, path)
        return f"{stem}_{_slug(lbl)}" if lbl else f"{stem}_{path.replace('.', '_')}"
    lbl = kb.element_label(tag, path)
    base = _slug(lbl) if lbl else f"{tag.lower()}_{path.replace('.', '_')}"
    return f"{_slug(kb.qualifier_label(tag, qual)) + '_' if qual and kb.qualifier_label(tag, qual) else (qual.lower() + '_' if qual else '')}{base}" if qual else base


def _observe(segs_per_msg: list[list[Segment]]) -> list[EdiSegmentDef]:
    tags_order: list[str] = []
    occ: dict[str, list[Segment]] = {}
    per_msg_presence: dict[str, int] = {}
    per_msg_max: dict[str, int] = {}
    for msg_segs in segs_per_msg:
        counts: dict[str, int] = {}
        for s in msg_segs:
            if s.tag not in tags_order:
                tags_order.append(s.tag)
            occ.setdefault(s.tag, []).append(s)
            counts[s.tag] = counts.get(s.tag, 0) + 1
        for t, c in counts.items():
            per_msg_presence[t] = per_msg_presence.get(t, 0) + 1
            per_msg_max[t] = max(per_msg_max.get(t, 0), c)

    defs: list[EdiSegmentDef] = []
    n_msgs = max(1, len(segs_per_msg))
    for tag in tags_order:
        segs = occ[tag]
        quals = {get(s, "1.1") for s in segs}
        known = any((tag, q) in kb.QUALIFIERS for q in quals)
        qualified = known or (len(quals) > 1 and all(0 < len(q) <= 3 for q in quals))
        status = "M" if per_msg_presence.get(tag, 0) == n_msgs else "C"
        max_occ = per_msg_max.get(tag, 1)
        if qualified and tag != "UNS":
            when: dict[str, EdiVariant] = {}
            for q in sorted(quals):
                variants_segs = [s for s in segs if get(s, "1.1") == q]
                fields: list[EdiField] = []
                fmt: Optional[str] = None
                if tag == "DTM":
                    fmts = {get(s, "1.3") for s in variants_segs if get(s, "1.3")}
                    fmt = next(iter(fmts), None) if len(fmts) == 1 and next(iter(fmts)) in kb.DATE_FORMATS else None
                paths: list[str] = []
                for s in variants_segs:
                    for ei, comps in enumerate(s.elements, start=1):
                        for ci, val in enumerate(comps, start=1):
                            p = f"{ei}.{ci}"
                            if val and p != "1.1" and p not in paths:
                                if tag == "DTM" and p == "1.3":
                                    continue
                                paths.append(p)
                for p in paths:
                    fields.append(EdiField(
                        name=_field_name(tag, q, p), path=p,
                        type="number" if tag in ("QTY", "PRI", "MOA", "CNT") and p == "1.2" else "text",
                        date_format=fmt if tag == "DTM" and p == "1.2" else None))
                when[q] = EdiVariant(label=kb.qualifier_label(tag, q), fields=fields)
            defs.append(EdiSegmentDef(tag=tag, status=status, max=max(max_occ, len(when)),
                                      qualifier="1.1", when=when))
        else:
            paths: list[str] = []
            for s in segs:
                for ei, comps in enumerate(s.elements, start=1):
                    for ci, val in enumerate(comps, start=1):
                        p = f"{ei}.{ci}"
                        if val and p not in paths:
                            paths.append(p)
            fields = [EdiField(name=_field_name(tag, "", p), path=p) for p in paths]
            if tag == "UNS":
                defs.append(EdiSegmentDef(tag=tag, status=status, max=1, qualifier="1.1",
                                          when={"S": EdiVariant(label="Début du résumé")}))
            else:
                defs.append(EdiSegmentDef(tag=tag, status=status, max=max_occ, fields=fields))
    return defs


def infer_model(seps: Separators, inters: list[Interchange], name: str = "") -> tuple[EdiModel, list[str]]:
    notes: list[str] = []
    msgs = [m for i in inters for m in i.messages]
    if not msgs:
        raise ValueError("Aucun message dans le fichier : impossible d'inférer un modèle.")
    mtype = msgs[0].mtype or "MESSAGE"
    directory = msgs[0].directory or "D96A"
    if len({m.mtype for m in msgs}) > 1:
        notes.append("Le fichier mélange plusieurs types de message : le modèle est inféré sur le premier type.")
        msgs = [m for m in msgs if m.mtype == mtype]

    loop_tag = "LIN" if any(s.tag == "LIN" for m in msgs for s in m.segments) else ""
    probe = EdiModel(name="probe", message_type=mtype,
                     items=EdiItems(loop_start=loop_tag or "LIN"),
                     summary=[EdiSegmentDef(tag=t) for t in ("UNS", "CNT", "MOA")])
    headers, loops_all, summaries = [], [], []
    for m in msgs:
        h, loops, su = _zones(m, probe)
        headers.append(h)
        loops_all.extend(loops)
        summaries.append(su)

    header_defs = _observe(headers)
    item_defs = _observe(loops_all) if loop_tag else []
    summary_defs = _observe([s for s in summaries if s]) if any(summaries) else []
    if not loop_tag:
        notes.append("Aucun segment LIN trouvé : modèle sans zone d'items.")

    env = EdiEnvelope(sender=inters[0].sender, recipient=inters[0].recipient)
    model = EdiModel(name=name or f"{mtype} {directory} (inféré)",
                     message_type=mtype, directory=directory, envelope=env,
                     header=header_defs,
                     items=EdiItems(loop_start=loop_tag or "LIN", segments=item_defs),
                     summary=summary_defs)
    notes.append(f"Inféré depuis {len(msgs)} message(s) : statuts M/C et occurrences max observés, à affiner.")
    return model, notes
