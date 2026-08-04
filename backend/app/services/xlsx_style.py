"""
xlsx_style.py
─────────────
Turn a `STYLE(color, bold, italic)` token into a real Excel cell format,
instead of discarding it at export time. The token is the exact same string
`duck_compute`/`style_rules.py` already compute per cell for the on-screen
grid — this module is the only place that reads it for XLSX, mirroring
`parseStyleToken` in `frontend/src/components/DataTable.tsx` (a bare token
with no `:`/`;` is a plain colour name; otherwise it's `key:value` pairs
joined by `;`).

`color` only ever set the on-screen *text* colour (never a background — the
background stays reserved for validation status everywhere else in this
app), so the Excel equivalent is a `Font` colour, not a fill.
"""
from __future__ import annotations

import pandas as pd

# A pragmatic set of CSS colour names to Excel's 6-hex-digit RGB — covers what
# someone is likely to type into a STYLE() call by hand. A name outside this
# list, or a bad value, is silently ignored (best-effort formatting, never a
# reason to fail the export) — a hex code (with or without '#') always works.
_CSS_COLORS = {
    "red": "FF0000", "orange": "FFA500", "yellow": "FFD700", "green": "008000",
    "blue": "0000FF", "purple": "800080", "pink": "FFC0CB", "gray": "808080",
    "grey": "808080", "black": "000000", "white": "FFFFFF", "brown": "A52A2A",
    "cyan": "00FFFF", "magenta": "FF00FF", "lime": "00FF00", "navy": "000080",
    "teal": "008080", "maroon": "800000", "olive": "808000", "gold": "FFD700",
    "indigo": "4B0082", "violet": "EE82EE", "salmon": "FA8072", "coral": "FF7F50",
}


def _hex_color(v: str) -> str | None:
    """Returns an 8-digit ARGB string, opaque (`FF` alpha) — openpyxl's
    `Font(color=...)` silently defaults a bare 6-digit RGB to `00` alpha
    (fully transparent), which renders as invisible text in real Excel, not
    as the colour asked for. Caught by actually opening the exported file,
    not just by the token parsing alone looking right."""
    v = v.strip().lstrip("#")
    rgb = v.upper() if (len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v)) \
        else _CSS_COLORS.get(v.lower())
    return f"FF{rgb}" if rgb else None


def parse_style_token(token) -> dict:
    """Same shape as the frontend's `parseStyleToken`: {} for nothing to
    apply, else a subset of {"color", "bold", "italic"}."""
    if not token or not isinstance(token, str):
        return {}
    t = token.strip()
    if not t:
        return {}
    if ":" not in t and ";" not in t:
        return {"color": t}
    out: dict = {}
    for part in t.split(";"):
        if ":" not in part:
            continue
        k, v = (x.strip() for x in part.split(":", 1))
        if k == "color" and v:
            out["color"] = v
        elif k == "bold" and v == "1":
            out["bold"] = True
        elif k == "italic" and v == "1":
            out["italic"] = True
    return out


def apply_xlsx_styles(ws, out_df: pd.DataFrame, style_map: dict[str, pd.Series]) -> None:
    """Apply `style_map` (column -> per-row token, aligned to `out_df`'s own
    index) as real cell fonts on `ws`, an openpyxl worksheet already holding
    `out_df` written with a header row at row 1 (pandas' `to_excel` layout)."""
    from openpyxl.styles import Font

    col_position = {c: i for i, c in enumerate(out_df.columns)}
    for col, series in style_map.items():
        pos = col_position.get(col)
        if pos is None or series is None:
            continue
        values = series.reindex(out_df.index)
        for row_i, token in enumerate(values.tolist()):
            style = parse_style_token(token)
            if not style:
                continue
            color = _hex_color(style["color"]) if style.get("color") else None
            cell = ws.cell(row=row_i + 2, column=pos + 1)  # +1 header row, +1 1-indexed
            cell.font = Font(color=color, bold=style.get("bold", False), italic=style.get("italic", False))
