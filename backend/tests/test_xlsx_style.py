"""
A STYLE() token computed for the on-screen grid should also become a real
Excel font when exporting to .xlsx with style=1 — the same token, applied
once more, not recomputed. Off (the default) or exporting to .csv leaves the
existing plain export untouched.
"""
import io

import openpyxl
import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.xlsx_style import apply_xlsx_styles, parse_style_token

client = TestClient(app)


def _no_explicit_rgb(cell) -> bool:
    """An untouched cell's font.color is a theme reference (type='theme'),
    never a bare None — and openpyxl's `.rgb` accessor itself raises on a
    theme-type Color rather than returning None, so `.type` is the only safe
    way to ask "was an explicit colour ever set here?"."""
    color = cell.font.color
    return color is None or getattr(color, "type", None) != "rgb"


CSV = "NOM;AGE\nAlice;17\nBob;34\n"
FIELDS = {"NOM": {"name": ["NOM"], "type": "string"}, "AGE": {"name": ["AGE"], "type": "string"}}
COLS = ["NOM", "AGE"]


def _upload():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# ── token parsing ──────────────────────────────────────────────────────

def test_parse_bare_color_token():
    assert parse_style_token("orange") == {"color": "orange"}


def test_parse_full_token():
    assert parse_style_token("color:blue;bold:1;italic:0") == {"color": "blue", "bold": True}


def test_parse_empty_or_none_token():
    assert parse_style_token("") == {}
    assert parse_style_token(None) == {}
    assert parse_style_token("color:;bold:0;italic:0") == {}


# ── applying to a real worksheet ────────────────────────────────────────

def test_apply_xlsx_styles_sets_an_opaque_font_color():
    df = pd.DataFrame({"nom": ["Alice", "Bob"], "age": ["17", "34"]})
    wb = openpyxl.Workbook()
    ws = wb.active
    for j, c in enumerate(df.columns):
        ws.cell(row=1, column=j + 1, value=c)
    for i, row in enumerate(df.itertuples(index=False)):
        for j, v in enumerate(row):
            ws.cell(row=i + 2, column=j + 1, value=v)
    apply_xlsx_styles(ws, df, {"age": pd.Series(["color:orange;bold:1", ""], index=df.index)})
    styled = ws.cell(row=2, column=2)
    plain = ws.cell(row=3, column=2)
    # a bare 6-digit RGB defaults to fully transparent in openpyxl unless the
    # alpha channel is set explicitly — this is the bug that would make the
    # colour invisible in real Excel despite the token parsing correctly.
    assert styled.font.color.rgb == "FFFFA500"
    assert styled.font.bold is True
    assert plain.font.bold is not True


def test_unknown_column_in_style_map_is_ignored_not_a_crash():
    df = pd.DataFrame({"nom": ["Alice"]})
    wb = openpyxl.Workbook()
    ws = wb.active
    apply_xlsx_styles(ws, df, {"does_not_exist": pd.Series(["orange"], index=df.index)})  # no raise


# ── end-to-end via /process + /export ───────────────────────────────────

def test_export_without_style_flag_has_no_font_color():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "style_rules": [{"column": "AGE", "expression":
            'IF([AGE] < "18", STYLE("orange", "1"), STYLE())'}]})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/files/{sid}/export", params={"fmt": "xlsx"})
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert _no_explicit_rgb(ws.cell(row=2, column=2))


def test_export_with_style_flag_applies_the_computed_style():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "style_rules": [{"column": "AGE", "expression":
            'IF([AGE] < "18", STYLE("orange", "1"), STYLE())'}]})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/files/{sid}/export", params={"fmt": "xlsx", "style": "true"})
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb.active
    # header row 1, Alice/17 row 2 — under 18, so STYLE("orange","1") applies
    assert ws.cell(row=2, column=2).font.color.rgb == "FFFFA500"
    assert ws.cell(row=2, column=2).font.bold is True
    # Bob/34 (row 3) is not under 18 — STYLE() with no args, no colour
    assert _no_explicit_rgb(ws.cell(row=3, column=2))


def test_export_style_flag_with_no_rules_does_not_crash():
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={"visible_cols": COLS, "fields": FIELDS})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/files/{sid}/export", params={"fmt": "xlsx", "style": "true"})
    assert r.status_code == 200
    openpyxl.load_workbook(io.BytesIO(r.content))  # doesn't raise
