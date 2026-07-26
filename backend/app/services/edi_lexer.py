"""
edi_lexer.py
────────────
Low-level EDIFACT lexer — no model required.

Responsibilities:
  • detect the family of an unknown file by CONTENT (extensions mean nothing
    in EDI): EDIFACT starts with UNA or UNB, X12 with a fixed-width ISA.
  • split a byte/text stream into segments/elements/components, honouring the
    UNA service string (custom separators) and the release (escape) character.
  • the exact inverse: render structured segments back to EDIFACT text with
    proper escaping — the round-trip parse(render(x)) == x is tested.

The lexer is deliberately family-agnostic in shape (separators are data, not
constants) so an X12 profile can plug in later without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Separators:
    component: str = ":"
    element: str = "+"
    decimal: str = "."
    release: str = "?"
    segment: str = "'"

    def as_dict(self) -> dict:
        return {"component": self.component, "element": self.element,
                "decimal": self.decimal, "release": self.release,
                "segment": self.segment}


@dataclass
class Segment:
    tag: str
    elements: list[list[str]] = field(default_factory=list)  # elements[i] = list of components
    pos: int = 0                                             # 1-based ordinal in the file
    raw: str = ""


class EdiSyntaxError(Exception):
    pass


def detect_format(text: str) -> str:
    """'EDIFACT' | 'X12' | 'UNKNOWN' — by content, never by extension."""
    head = text.lstrip("\ufeff \t\r\n")
    if head.startswith("UNA") and len(head) >= 9:
        return "EDIFACT"
    if head.startswith("UNB") and len(head) > 3 and not head[3].isalnum():
        return "EDIFACT"
    if head.startswith("ISA") and len(head) >= 106:
        return "X12"
    return "UNKNOWN"


def read_una(text: str) -> tuple[Separators, str]:
    """If the stream starts with UNA, read the 6 service chars and return the
    remainder; otherwise return defaults and the stream untouched."""
    head = text.lstrip("\ufeff \t\r\n")
    if head.startswith("UNA") and len(head) >= 9:
        s = head[3:9]
        seps = Separators(component=s[0], element=s[1], decimal=s[2],
                          release=s[3], segment=s[5])
        return seps, head[9:]
    return Separators(), head


def parse(text: str) -> tuple[Separators, list[Segment], bool]:
    """Full lex: returns (separators, segments, had_una)."""
    stripped = text.lstrip("\ufeff \t\r\n")
    had_una = stripped.startswith("UNA")
    seps, body = read_una(text)

    segments: list[Segment] = []
    comp: list[str] = []          # chars of current component
    element: list[str] = []       # components of current element
    seg_elements: list[list[str]] = []
    raw_chars: list[str] = []
    in_segment = False
    i, n = 0, len(body)

    def close_component():
        element.append("".join(comp))
        comp.clear()

    def close_element():
        close_component()
        seg_elements.append(list(element))
        element.clear()

    def close_segment():
        close_element()
        # trim trailing empty components/elements for a canonical shape
        parts = [list(e) for e in seg_elements]
        for e in parts:
            while len(e) > 1 and e[-1] == "":
                e.pop()
        while len(parts) > 1 and parts[-1] == [""]:
            parts.pop()
        tag = parts[0][0] if parts and parts[0] else ""
        segments.append(Segment(tag=tag, elements=parts[1:],
                                pos=len(segments) + 1,
                                raw="".join(raw_chars)))
        seg_elements.clear()
        raw_chars.clear()

    while i < n:
        ch = body[i]
        if not in_segment:
            if ch in " \t\r\n":          # whitespace BETWEEN segments only
                i += 1
                continue
            in_segment = True
        raw_chars.append(ch)
        if ch == seps.release:
            if i + 1 >= n:
                raise EdiSyntaxError(f"Caractère d'échappement '{seps.release}' en fin de fichier (segment {len(segments)+1}).")
            i += 1
            raw_chars.append(body[i])
            comp.append(body[i])
        elif ch == seps.component:
            close_component()
        elif ch == seps.element:
            close_element()
        elif ch == seps.segment:
            raw_chars.pop()               # keep raw without the terminator
            close_segment()
            in_segment = False
        else:
            comp.append(ch)
        i += 1

    if in_segment and (comp or element or seg_elements):
        raise EdiSyntaxError(f"Fin de fichier au milieu d'un segment (après le segment {len(segments)}) : terminateur '{seps.segment}' manquant.")
    return seps, segments, had_una


def get(seg: Segment, path: str) -> str:
    """Read 'element.component' (both 1-based) from a segment; '' if absent."""
    try:
        e_s, c_s = path.split(".")
        e, c = int(e_s) - 1, int(c_s) - 1
    except Exception:  # noqa: BLE001
        return ""
    if e < 0 or e >= len(seg.elements):
        return ""
    comps = seg.elements[e]
    return comps[c] if 0 <= c < len(comps) else ""


def escape(value: str, seps: Separators) -> str:
    out = []
    specials = {seps.component, seps.element, seps.segment, seps.release}
    for ch in str(value):
        if ch in "\r\n":
            ch = " "
        if ch in specials:
            out.append(seps.release)
        out.append(ch)
    return "".join(out)


def render_segment(tag: str, elements: list[list[str]], seps: Separators) -> str:
    parts = []
    for comps in elements:
        cs = [escape(c, seps) for c in comps]
        while len(cs) > 1 and cs[-1] == "":
            cs.pop()
        parts.append(seps.component.join(cs))
    while parts and parts[-1] == "":
        parts.pop()
    body = seps.element.join([tag] + parts)
    return body + seps.segment


def render(segments: list[tuple[str, list[list[str]]]], seps: Separators | None = None,
           una: bool = True) -> str:
    seps = seps or Separators()
    lines = []
    if una:
        lines.append(f"UNA{seps.component}{seps.element}{seps.decimal}{seps.release} {seps.segment}")
    for tag, elements in segments:
        lines.append(render_segment(tag, elements, seps))
    return "\n".join(lines) + "\n"
