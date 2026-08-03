"""
End-to-end API tests. Exercise the real flow: upload, header, configure,
process, report — plus YAML round-trip and TCO mapping.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ── fixtures ──────────────────────────────────────────────────

CSV = (
    "SIRET;CIVILITE;DATE_NAISSANCE;MONTANT\n"
    "12345678901234;M;01/02/1990;1 234,50\n"
    "999;F;31/12/1985;42,00\n"          # SIRET too short -> ERROR
    "12345678901234;X;1990-13-01;abc\n"  # bad civilite, bad date, bad amount
)

TCO = (
    "SOURCE_VALUE;TARGET_LABEL\n"
    "M;MASCULIN\n"
    "F;FEMININ\n"
)


def _upload(csv: str = CSV):
    r = client.post(
        "/api/files",
        files={"file": ("data.csv", io.BytesIO(csv.encode()), "text/csv")},
        data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── tests ─────────────────────────────────────────────────────

def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_presets():
    body = client.get("/api/presets").json()
    assert "SIRET (14 chiffres)" in body["regex_presets"]
    assert "date" in body["field_types"]


def test_upload_returns_columns_and_preview():
    body = _upload()
    assert body["preview"]["columns"] == ["SIRET", "CIVILITE", "DATE_NAISSANCE", "MONTANT"]
    assert body["preview"]["total_rows"] == 3
    assert body["session_id"]


def test_length_and_regex_validation():
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {
            "name": ["SIRET"], "type": "string",
            "length": 14, "regex": r"^\d{14}$", "nullable": False,
            "identifiant": True,
        }},
        "identifier_field": "SIRET",
    })
    assert r.status_code == 200, r.text
    out = r.json()
    statuses = [row[0] for row in out["status"]]
    assert statuses == ["OK", "ERROR", "OK"]   # row 2 (SIRET=999) fails
    assert out["stats"]["per_col"]["SIRET"]["errors"] == 1


def test_number_cleaning_marks_cleaned():
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["MONTANT"],
        "fields": {"MONTANT": {
            "name": ["MONTANT"], "type": "float",
            "separator_mile": True, "separator_decimal": True,
            "check_type": True,
        }},
    })
    out = r.json()
    # "1 234,50" -> "1234.50" (cleaned); "abc" -> type error
    col_data = [row[0] for row in out["data"]]
    col_status = [row[0] for row in out["status"]]
    assert col_data[0] == "1234.50"
    assert col_status[0] == "CLEANED"
    assert col_status[2] == "ERROR"


def test_date_reformat():
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["DATE_NAISSANCE"],
        "fields": {"DATE_NAISSANCE": {
            "name": ["DATE_NAISSANCE"], "type": "date",
            "format": "%d/%m/%Y", "format_clean": "%Y-%m-%d",
            "check_type": True,
        }},
    })
    out = r.json()
    data = [row[0] for row in out["data"]]
    assert data[0] == "1990-02-01"


def test_tco_mapping():
    sid = _upload()["session_id"]
    client.post(
        f"/api/files/{sid}/tco",
        files={"file": ("tco.csv", io.BytesIO(TCO.encode()), "text/csv")},
        data={"delimiter": ";"},
    )
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "tco_mapping": "MASCULIN"}},
    })
    out = r.json()
    statuses = [row[0] for row in out["status"]]
    # row0 M->MASCULIN ok ; row1 F->FEMININ (expected MASCULIN) ko ; row2 X ko
    assert statuses[0] == "MAPPING_OK"
    assert statuses[1] == "MAPPING_KO"
    assert statuses[2] == "MAPPING_KO"


def test_yaml_export_import_roundtrip():
    fields = {"SIRET": {
        "name": ["SIRET"], "type": "string", "length": 14,
        "regex": r"^\d{14}$", "nullable": False, "identifiant": True,
    }}
    exp = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";", "encoding": "utf-8",
        "header": {}, "fields": fields, "visible_cols": ["SIRET"],
    }).json()
    assert "SIRET" in exp["yaml"]

    imp = client.post("/api/config/import", json={
        "yaml": exp["yaml"], "columns": ["SIRET", "CIVILITE"],
    }).json()
    assert imp["file_config"]["Fields"][0]["length"] == 14
    assert "SIRET" in imp["match"]["matched"]
    assert "CIVILITE" in imp["match"]["unused"]


def test_header_drops_empty_lines():
    csv = "\n\nA;B\n1;2\n\n"
    sid = _upload(csv)["session_id"]
    r = client.post(f"/api/files/{sid}/header", json={
        "header": {"delete_all_empty_line": True}
    })
    assert r.status_code == 200, r.text


def test_missing_session_is_404():
    r = client.post("/api/files/nope/process", json={"visible_cols": [], "fields": {}})
    assert r.status_code == 404


def test_computed_columns():
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "type": "string"}},
        "computed": [
            {"name": "label", "expression": 'IF([CIVILITE] == "M", "Monsieur", "Madame")'},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert "label" in out["computed"]
    li = out["columns"].index("label")
    labels = [row[li] for row in out["data"]]
    assert labels[0] == "Monsieur"   # M
    assert labels[1] == "Madame"     # F
    assert out["status"][0][li] == "COMPUTED"


def test_expression_check_rejects_unsafe():
    ok = client.post("/api/expression/check", json={"expression": 'CONCAT([a], [b])'}).json()
    assert ok["ok"] is True
    bad = client.post("/api/expression/check", json={"expression": '__import__("os").system("ls")'}).json()
    assert bad["ok"] is False
    assert bad["error"]


def test_delete_unnamed_with_data():
    # "A;;C" -> middle column has an empty header but real data; should be dropped.
    sid = _upload("A;;C\n1;2;3\n4;5;6\n")["session_id"]
    r = client.post(f"/api/files/{sid}/header", json={"header": {"delete_unamed_column": True}})
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["A", "C"]


def test_export_csv_roundtrip():
    sid = _upload()["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "CIVILITE"],
        "fields": {
            "SIRET": {"name": ["SIRET"], "type": "string"},
            "CIVILITE": {"name": ["CIVILITE"], "type": "string", "normalize_case": "lower"},
        },
    })
    r = client.get(f"/api/files/{sid}/export", params={"fmt": "csv", "delimiter": ",", "filename": "out"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert "SIRET,CIVILITE" in text
    assert "m" in text.lower()
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "regex": r"^\d{14}$", "nullable": False}},
    })
    statuses = {row["statut"] for row in r.json()["report"]}
    assert "OK" not in statuses          # OK cells are summarized in stats, not shipped
    assert "ERROR" in statuses


def test_empty_values_are_not_marked_cleaned():
    # A trailing-space value is cleaned; an empty value must NOT be.
    sid = _upload("NOM\n  jean  \n\nMARIE\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["NOM"],
        "fields": {"NOM": {"name": ["NOM"], "type": "string", "trim": True}},
    })
    out = r.json()
    statuses = [row[0] for row in out["status"]]
    assert statuses[0] == "CLEANED"   # "  jean  " -> "jean"
    assert statuses[1] == "OK"        # empty stays OK, not CLEANED


def test_function_library():
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"}},
        "computed": [
            {"name": "head", "expression": "LEFT([SIRET], 3)"},
            {"name": "part", "expression": 'SPLIT(CONCAT([SIRET], "-x"), "-", 1)'},
            {"name": "digits", "expression": 'REGEX_EXTRACT([SIRET], "[0-9]+")'},
        ],
    })
    out = r.json()
    assert {"head", "part", "digits"}.issubset(set(out["columns"]))
    assert out["data"][0][out["columns"].index("head")] == "123"
    assert out["data"][0][out["columns"].index("part")] == "x"


def test_lookup_uses_tco():
    sid = _upload()["session_id"]
    client.post(f"/api/files/{sid}/tco",
                files={"file": ("tco.csv", io.BytesIO(TCO.encode()), "text/csv")},
                data={"delimiter": ";"})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "type": "string"}},
        "computed": [{"name": "label", "expression": "LOOKUP([CIVILITE])"}],
    })
    out = r.json()
    labels = [row[out["columns"].index("label")] for row in out["data"]]
    assert labels[0] == "MASCULIN"
    assert labels[1] == "FEMININ"


def test_rename_output_toggle():
    # rename_output=True -> output column renamed; rules applied either way.
    sid = _upload()["session_id"]
    r1 = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "type": "string",
                                 "mapping": "GENRE", "rename_output": True}},
    })
    assert "GENRE" in r1.json()["columns"]
    assert "CIVILITE" not in r1.json()["columns"]

    # rename_output=False -> keep original name, but the length rule still runs.
    sid2 = _upload()["session_id"]
    r2 = client.post(f"/api/files/{sid2}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string", "length": 5,
                             "mapping": "IDENT", "rename_output": False}},
    })
    out = r2.json()
    assert "SIRET" in out["columns"]          # original name kept
    assert "IDENT" not in out["columns"]
    statuses = [row[0] for row in out["status"]]
    assert "ERROR" in statuses                # length=5 rule still enforced


def test_computed_sees_renamed_column():
    # Rename CIVILITE -> GENRE; a computed column must reference the NEW name.
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "type": "string",
                                 "mapping": "GENRE", "rename_output": True}},
        "computed": [
            {"name": "viaNew", "expression": 'UPPER([GENRE])'},
            {"name": "viaOld", "expression": 'UPPER([CIVILITE])'},
        ],
    })
    out = r.json()
    new_i = out["columns"].index("viaNew")
    old_i = out["columns"].index("viaOld")
    assert out["data"][0][new_i] == "M"     # [GENRE] resolves
    assert out["data"][0][old_i] == ""      # [CIVILITE] no longer exists -> empty


def test_rename_target_recognized_as_source_on_import():
    # A config field SIRET renamed to IDENTIFIANT should match a file that
    # already has a column named IDENTIFIANT.
    sid = _upload("IDENTIFIANT;X\n12345678901234;a\n")["session_id"]
    cfg = (
        'type: CSV\ndelimiter: ";"\nFields:\n'
        '  - name: [SIRET]\n    type: string\n    mapping: IDENTIFIANT\n'
    )
    imp = client.post("/api/config/import", json={"yaml": cfg, "columns": ["IDENTIFIANT", "X"]}).json()
    assert "IDENTIFIANT" in imp["match"]["matched"]


def test_tco_auto_detects_delimiter_and_aliases():
    # Comma-delimited TCO with alias headers SOURCE / LABEL must load.
    tco = "SOURCE,LABEL\nM,MASCULIN\nF,FEMININ\n"
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/tco",
                    files={"file": ("t.csv", io.BytesIO(tco.encode()), "text/csv")},
                    data={"delimiter": "AUTO", "encoding": "AUTO"})
    assert r.status_code == 200, r.text
    assert set(r.json()["labels"]) == {"MASCULIN", "FEMININ"}


def test_tco_bad_header_gives_clear_message():
    bad = "FOO;BAR\n1;2\n"
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/tco",
                    files={"file": ("t.csv", io.BytesIO(bad.encode()), "text/csv")},
                    data={"delimiter": "AUTO"})
    assert r.status_code == 422
    assert "SOURCE_VALUE" in r.json()["detail"] and "TARGET_LABEL" in r.json()["detail"]


def test_xlsx_multi_sheet_selection():
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        pd.DataFrame({"A": [1], "B": [2]}).to_excel(w, sheet_name="First", index=False)
        pd.DataFrame({"X": [9], "Y": [8], "Z": [7]}).to_excel(w, sheet_name="Second", index=False)
    buf.seek(0)
    r = client.post("/api/files",
                    files={"file": ("m.xlsx", buf, "application/vnd.ms-excel")},
                    data={"file_type": "XLSX", "sheet": "Second"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sheets"] == ["First", "Second"]
    assert body["sheet"] == "Second"
    assert body["preview"]["columns"] == ["X", "Y", "Z"]


def test_export_filtered_rows_only():
    sid = _upload()["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "CIVILITE"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"},
                   "CIVILITE": {"name": ["CIVILITE"], "type": "string"}},
    })
    import json as _j
    r = client.get(f"/api/files/{sid}/export",
                   params={"fmt": "csv", "delimiter": ",", "filters": _j.dumps({"CIVILITE": "M"})})
    assert r.status_code == 200
    lines = [ln for ln in r.content.decode().splitlines() if ln.strip()]
    # header + only rows where CIVILITE contains "M" (M row). F row excluded.
    assert lines[0] == "SIRET,CIVILITE"
    assert all("F" not in ln.split(",")[1] for ln in lines[1:])


def test_yaml_roundtrips_sheet_and_filters():
    exp = client.post("/api/config/export", json={
        "type": "XLSX", "delimiter": ";", "sheet": "Data",
        "header": {}, "fields": {"A": {"name": ["A"], "type": "string"}},
        "visible_cols": ["A"], "filters": {"A": "x"},
    }).json()
    assert "sheet: Data" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    assert imp["file_config"]["sheet"] == "Data"
    assert imp["file_config"]["filters"] == {"A": "x"}


def test_rename_collision_does_not_crash():
    # Renaming a column onto an existing column name must not 500; it should
    # keep the original name and return a warning.
    sid = _upload("A;B\n1;2\n3;4\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["A", "B"],
        "fields": {"A": {"name": ["A"], "mapping": "B", "rename_output": True},
                   "B": {"name": ["B"], "type": "string"}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "A" in body["columns"] and "B" in body["columns"]
    assert any("B" in w for w in body["warnings"])


def test_variables_and_dynamic_tokens():
    from datetime import datetime
    sid = _upload()["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"}},
        "variables": {"societe": "italie"},
        "computed": [
            {"name": "pays", "expression": "UPPER([societe])"},
            {"name": "mois", "expression": "[MOIS_NOM]"},
            {"name": "mix",  "expression": 'CONCAT([societe], "-", [ANNEE])'},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    pays = out["data"][0][out["columns"].index("pays")]
    mois = out["data"][0][out["columns"].index("mois")]
    assert pays == "ITALIE"
    assert mois  # current month name, non-empty
    yr = str(datetime.now().year)
    assert out["data"][0][out["columns"].index("mix")] == f"italie-{yr}"


def test_config_roundtrips_variables_and_strict_header():
    exp = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";", "strict_header": True,
        "variables": {"societe": "italie"},
        "header": {}, "fields": {"A": {"name": ["A"], "type": "string"}},
        "visible_cols": ["A"],
    }).json()
    assert "strict_header: true" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    assert imp["file_config"]["strict_header"] is True
    assert imp["file_config"]["variables"] == {"societe": "italie"}


def test_config_roundtrips_min_header():
    exp = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";", "min_header": True,
        "header": {}, "fields": {"A": {"name": ["A"], "type": "string"}},
        "visible_cols": ["A"],
    }).json()
    assert "min_header: true" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    assert imp["file_config"]["min_header"] is True


def test_strict_header_and_min_header_are_mutually_exclusive():
    r = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";", "strict_header": True, "min_header": True,
        "header": {}, "fields": {}, "visible_cols": [],
    })
    assert r.status_code == 422


def test_dynamic_column_name_matching():
    # A column named like the current short month is matched by a field whose
    # source name is the token [MOIS_COURT].
    from app.services.compute_service import dynamic_tokens
    mc = dynamic_tokens()["MOIS_COURT"]
    sid = _upload(f"{mc};AUTRE\nx;y\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": [mc],
        "fields": {mc: {"name": ["[MOIS_COURT]"], "type": "string", "mapping": "MOIS_DATA", "rename_output": True}},
    })
    assert r.status_code == 200, r.text
    # the dynamic name resolved and the column was processed (renamed here)
    assert "MOIS_DATA" in r.json()["columns"]


def test_dynamic_name_matching_on_import():
    from app.services.compute_service import dynamic_tokens
    mc = dynamic_tokens()["MOIS_COURT"]
    cfg = 'type: CSV\ndelimiter: ";"\nFields:\n  - name: ["[MOIS_COURT]"]\n    type: string\n'
    imp = client.post("/api/config/import", json={"yaml": cfg, "columns": [mc, "AUTRE"]}).json()
    assert mc in imp["match"]["matched"]


def _multi_table_xlsx():
    import pandas as pd
    # Sheet with two tables separated by a marker row "=== VENTES ==="
    rows = [
        ["NOM", "VILLE"],
        ["jean", "Paris"],
        ["marie", "Lyon"],
        ["Tableau VENTES", ""],
        ["luc", "Nice"],
        ["zoe", "Brest"],
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf


def test_excel_table_split_local_header():
    buf = _multi_table_xlsx()
    r = client.post("/api/files",
                    files={"file": ("m.xlsx", buf, "application/vnd.ms-excel")},
                    data={"file_type": "XLSX", "table_marker": "VENTES",
                          "table_index": "1", "table_header_mode": "local"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["table_count"] == 2
    # table index 1, local header => its first row "luc/Nice" is the header
    assert body["preview"]["columns"] == ["luc", "Nice"]
    assert body["preview"]["data"][0] == ["zoe", "Brest"]


def test_excel_table_split_global_header():
    buf = _multi_table_xlsx()
    r = client.post("/api/files",
                    files={"file": ("m.xlsx", buf, "application/vnd.ms-excel")},
                    data={"file_type": "XLSX", "table_marker": "VENTES",
                          "table_index": "1", "table_header_mode": "global"})
    body = r.json()
    # global header => columns from first table (NOM/VILLE); table 1 keeps all rows as data
    assert body["preview"]["columns"] == ["NOM", "VILLE"]
    assert ["luc", "Nice"] in body["preview"]["data"]
    assert ["zoe", "Brest"] in body["preview"]["data"]


def test_config_roundtrips_table_settings():
    exp = client.post("/api/config/export", json={
        "type": "XLSX", "delimiter": ";",
        "table_marker": "VENTES", "table_index": 1, "table_header_mode": "global",
        "header": {}, "fields": {"A": {"name": ["A"], "type": "string"}}, "visible_cols": ["A"],
    }).json()
    assert "table_marker: VENTES" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    fc = imp["file_config"]
    assert fc["table_marker"] == "VENTES" and fc["table_index"] == 1 and fc["table_header_mode"] == "global"


def test_rows_pagination_and_filter_count():
    # Build a 25-row file, process, then page + filter over the full set.
    rows = "VILLE\n" + "\n".join(f"V{i:02d}" for i in range(25)) + "\n"
    sid = _upload(rows)["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["VILLE"],
        "fields": {"VILLE": {"name": ["VILLE"], "type": "string"}},
    })
    # page 1: 10 rows out of 25
    r1 = client.get(f"/api/files/{sid}/rows", params={"offset": 0, "limit": 10}).json()
    assert r1["total"] == 25 and r1["total_all"] == 25
    assert len(r1["data"]) == 10
    # page 3: remaining 5
    r3 = client.get(f"/api/files/{sid}/rows", params={"offset": 20, "limit": 10}).json()
    assert len(r3["data"]) == 5
    # filter applies to the whole file
    import json as _j
    rf = client.get(f"/api/files/{sid}/rows", params={"limit": 100, "filters": _j.dumps({"VILLE": "V1"})}).json()
    assert rf["total"] == 10 and rf["total_all"] == 25   # V10..V19
    # sort desc
    rs = client.get(f"/api/files/{sid}/rows", params={"limit": 3, "sort_col": "VILLE", "sort_dir": "desc"}).json()
    assert rs["data"][0][0] == "V24"


def test_dynamic_name_with_function():
    # A column named with the first 4 letters of the current month name,
    # matched by an expression =LEFT([MOIS_NOM], 4).
    from app.services.compute_service import dynamic_tokens
    head = dynamic_tokens()["MOIS_NOM"][:4]
    sid = _upload(f"{head};X\na;b\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": [head],
        "fields": {head: {"name": ["=LEFT([MOIS_NOM], 4)"], "type": "string"}},
    })
    assert r.status_code == 200, r.text
    assert head in r.json()["columns"]


def test_exists_and_col_cascade():
    # File has 'fevr' but not 'janv'. Cascade should pick the existing column.
    sid = _upload("fevr;X\n12;a\n7;b\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["fevr"],
        "fields": {"fevr": {"name": ["fevr"], "type": "string"}},
        "computed": [
            {"name": "has_jan", "expression": 'EXISTS("janv")'},
            {"name": "has_fev", "expression": 'EXISTS("fevr")'},
            {"name": "casc", "expression": 'IF(EXISTS("janv"), COL("janv"), COL("fevr"))'},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    cols = out["columns"]
    assert out["data"][0][cols.index("has_jan")] == ""    # janv absent
    assert out["data"][0][cols.index("has_fev")] == "1"    # fevr present
    assert out["data"][0][cols.index("casc")] == "12"      # cascades to fevr value


def test_declared_absent_column_is_ignored():
    # Two fields declared (janv, fevr) but the file only has fevr.
    # Non-strict: janv is simply skipped, fevr is processed. No crash.
    sid = _upload("fevr\n5\n12\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["janv", "fevr"],
        "fields": {
            "janv": {"name": ["janv"], "type": "integer"},
            "fevr": {"name": ["fevr"], "type": "integer"},
        },
    })
    assert r.status_code == 200, r.text
    cols = r.json()["columns"]
    assert "fevr" in cols and "janv" not in cols


# ── one-shot pipeline endpoint ────────────────────────────────────────
import base64 as _b64

def _pipe(config, csv_text, tco=None, computed=None, filename="out"):
    files = {"file": ("data.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    if tco is not None:
        files["tco"] = ("tco.csv", io.BytesIO(tco.encode()), "text/csv")
    data = {"config": config, "export_filename": filename}
    if computed is not None:
        data["computed"] = computed
    return client.post("/api/pipeline", files=files, data=data)


def test_pipeline_happy_path_exports():
    cfg = ('type: CSV\ndelimiter: ";"\nFields:\n'
           '  - name: ["AGE"]\n    type: integer\n')
    r = _pipe(cfg, "AGE\n30\n45\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["stage"] == "done"
    assert body["export"]["rows_exported"] == 2
    decoded = _b64.b64decode(body["export"]["content_base64"]).decode()
    assert "AGE" in decoded and "30" in decoded


def test_pipeline_strict_header_missing_column():
    cfg = ('type: CSV\ndelimiter: ";"\nstrict_header: true\nFields:\n'
           '  - name: ["AGE"]\n    type: integer\n'
           '  - name: ["VILLE"]\n    type: string\n')
    r = _pipe(cfg, "AGE\n30\n")              # VILLE missing
    body = r.json()
    assert body["ok"] is False and body["stage"] == "strict_header"
    assert "VILLE" in body["missing_columns"]
    assert body["export"] is None


def test_pipeline_min_header_missing_column_blocks():
    cfg = ('type: CSV\ndelimiter: ";"\nmin_header: true\nFields:\n'
           '  - name: ["AGE"]\n    type: integer\n'
           '  - name: ["VILLE"]\n    type: string\n')
    r = _pipe(cfg, "AGE\n30\n")              # VILLE missing
    body = r.json()
    assert body["ok"] is False and body["stage"] == "min_header"
    assert "VILLE" in body["missing_columns"]
    assert body["export"] is None


def test_pipeline_min_header_tolerates_extra_column():
    cfg = ('type: CSV\ndelimiter: ";"\nmin_header: true\nFields:\n'
           '  - name: ["AGE"]\n    type: integer\n')
    r = _pipe(cfg, "AGE;VILLE\n30;Paris\n")   # VILLE unexpected but tolerated
    body = r.json()
    assert body["ok"] is True and body["stage"] == "done"


def test_pipeline_missing_tco_errors():
    cfg = ('type: CSV\ndelimiter: ";"\nFields:\n'
           '  - name: ["CAT"]\n    type: string\n    tco_mapping: "Famille"\n')
    r = _pipe(cfg, "CAT\nx\n")               # no TCO provided but mapping required
    body = r.json()
    assert body["ok"] is False and body["stage"] == "tco"


def test_pipeline_validation_error_grouped_by_id():
    cfg = ('type: CSV\ndelimiter: ";"\nFields:\n'
           '  - name: ["ID"]\n    type: string\n    identifier: true\n'
           '  - name: ["AGE"]\n    type: integer\n    check_type: true\n')
    # second row AGE is not an integer -> ERROR, keyed by ID
    r = _pipe(cfg, "ID;AGE\nA1;30\nA2;oops\n")
    body = r.json()
    assert body["ok"] is False and body["stage"] == "validation"
    assert body["identifier_field"] == "ID"
    ids = {row["id"] for row in body["report"]}
    assert "A2" in ids
    assert body["export"] is None


def test_pipeline_compute_error_reported():
    cfg = ('type: CSV\ndelimiter: ";"\nFields:\n  - name: ["X"]\n    type: string\n')
    bad = '[{"name": "boom", "expression": "THIS_FN_DOES_NOT_EXIST(1)"}]'
    r = _pipe(cfg, "X\na\n", computed=bad)
    body = r.json()
    # a malformed expression surfaces as a compute error and blocks export
    assert body["ok"] is False
    assert body["compute_errors"] != {}
    assert body["export"] is None


def test_identifier_roundtrips_in_config():
    # A field marked as identifier must serialize to YAML `identifier: true`
    # and come back as identifiant=True on import.
    exp = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";",
        "header": {}, "visible_cols": ["ID", "AGE"],
        "fields": {
            "ID": {"name": ["ID"], "type": "string", "identifiant": True},
            "AGE": {"name": ["AGE"], "type": "integer"},
        },
    }).json()
    assert "identifier: true" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    id_field = next(f for f in imp["file_config"]["Fields"] if f["name"] == ["ID"])
    assert id_field["identifiant"] is True


def test_compute_null_handling_in_if():
    # An empty cell must be detected as null by ISNULL and behave as "" in IF,
    # not as the literal text "nan".
    sid = _upload("A;B\nx;\n;y\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["A", "B"],
        "fields": {"A": {"name": ["A"]}, "B": {"name": ["B"]}},
        "computed": [
            {"name": "b_or_na", "expression": 'IF(ISNULL([B]), "N/A", [B])'},
            {"name": "a_empty", "expression": 'IF([A] == "", "vide", "plein")'},
            {"name": "has_b", "expression": 'NOTNULL([B])'},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json(); cols = out["columns"]
    # row 1: B empty -> N/A ; A non-empty -> plein ; has_b empty
    assert out["data"][0][cols.index("b_or_na")] == "N/A"
    assert out["data"][0][cols.index("a_empty")] == "plein"
    assert out["data"][0][cols.index("has_b")] == ""
    # row 2: B present -> y ; A empty -> vide ; has_b "1"
    assert out["data"][1][cols.index("b_or_na")] == "y"
    assert out["data"][1][cols.index("a_empty")] == "vide"
    assert out["data"][1][cols.index("has_b")] == "1"


def test_filter_operators():
    import json as _j
    rows = "VILLE;AGE\nLyon;30\nParis;45\nLyon;22\nNice;\nlyon;60\n"
    sid = _upload(rows)["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["VILLE", "AGE"],
        "fields": {"VILLE": {"name": ["VILLE"]}, "AGE": {"name": ["AGE"]}},
    })
    def rows_for(f):
        return client.get(f"/api/files/{sid}/rows", params={"limit": 100, "filters": _j.dumps(f)}).json()["total"]
    assert rows_for({"VILLE": "lyon"}) == 3          # contains, case-insensitive
    assert rows_for({"VILLE": "=lyon"}) == 3         # equals (ci) -> Lyon, Lyon, lyon
    assert rows_for({"VILLE": "!lyon"}) == 2         # not contains -> Paris, Nice
    assert rows_for({"VILLE": "in:paris,nice"}) == 2
    assert rows_for({"VILLE": "!in:lyon"}) == 2      # not in -> Paris, Nice
    assert rows_for({"AGE": ">40"}) == 2             # 45, 60
    assert rows_for({"AGE": ">=45"}) == 2
    assert rows_for({"AGE": "<30"}) == 1             # 22 (empty excluded)
    assert rows_for({"AGE": "empty"}) == 1           # Nice
    assert rows_for({"AGE": "!empty"}) == 4


def test_tco_uncovered_values_listed():
    # TCO covers M and F; the file also has "X" (unknown) -> uncovered, with count.
    sid = _upload("CIVILITE\nM\nX\nF\nX\n")["session_id"]
    client.post(f"/api/files/{sid}/tco",
                files={"file": ("tco.csv", io.BytesIO(TCO.encode()), "text/csv")},
                data={"source_col": "SOURCE_VALUE", "target_col": "TARGET_LABEL"})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "tco_mapping": "MASCULIN"}},
    })
    assert r.status_code == 200, r.text
    unc = r.json()["tco_uncovered"]
    assert "CIVILITE" in unc
    vals = {d["value"]: d["count"] for d in unc["CIVILITE"]}
    assert vals.get("X") == 2


def test_tco_field_matching_no_column_warns_instead_of_silently_skipping():
    # "job" is declared but the file only has "POSTE" — the field silently
    # matches nothing today unless this warns: no error, no NO_TCO, nothing,
    # which reads as "fine" when it was never checked at all.
    sid = _upload("POSTE\ncomptable\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["job"],
        "fields": {"job": {"name": ["job"], "tco_replace": True, "tco_type": "job"}},
    })
    assert r.status_code == 200, r.text
    assert any("job" in w for w in r.json()["warnings"])


def test_a_field_keyed_differently_from_its_real_column_is_not_silently_dropped():
    # The field's dict key ("civility") need not equal the file's actual
    # header ("CIV") — `name` is exactly what resolves that. A prior bug
    # required the key itself to already be a literal df column, which
    # silently dropped any field declared under a different key before that
    # resolution ever ran (no error, no report row — just invisible).
    sid = _upload("CIV\nM\nF\n")["session_id"]
    client.post(f"/api/files/{sid}/tco",
                files={"file": ("tco.csv", io.BytesIO(TCO.encode()), "text/csv")},
                data={"source_col": "SOURCE_VALUE", "target_col": "TARGET_LABEL"})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["civility"],
        "fields": {"civility": {"name": ["CIV"], "tco_replace": True}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["warnings"]
    col = body["columns"].index("CIV")
    assert [row[col] for row in body["data"]] == ["MASCULIN", "FEMININ"]


def test_tco_configured_field_with_no_tco_loaded_surfaces_as_uncovered():
    # A field asks for tco_mapping but no TCO was ever attached to this
    # session — every value must show up as NO_TCO, distinctly from a
    # genuine "value missing from an otherwise-loaded table" (MAPPING_KO).
    # Silently omitting it would read as "fully covered" when nothing was
    # ever checked at all.
    sid = _upload("CIVILITE\nM\nF\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIVILITE"],
        "fields": {"CIVILITE": {"name": ["CIVILITE"], "tco_mapping": "MASCULIN"}},
    })
    assert r.status_code == 200, r.text
    unc = r.json()["tco_uncovered"]
    assert "CIVILITE" in unc
    assert unc["CIVILITE"][0]["reason"] == "no_tco"


def test_multiple_identifier_fields_compose_report_id():
    cfg = ('type: CSV\ndelimiter: ";"\nFields:\n'
           '  - name: ["DEPT"]\n    type: string\n    identifier: true\n'
           '  - name: ["NUM"]\n    type: string\n    identifier: true\n'
           '  - name: ["AGE"]\n    type: integer\n    check_type: true\n')
    r = _pipe(cfg, "DEPT;NUM;AGE\n26;A1;30\n75;B2;oops\n")
    body = r.json()
    assert body["ok"] is False
    ids = {row["id"] for row in body["report"]}
    assert "75 | B2" in ids          # composite id from two identifier fields


def test_report_endpoint_shapes_and_filter():
    sid = _upload("ID;AGE;CITY\nA1;30;Lyon\nA2;oops;Paris\nA3;abc;Lyon\n")["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["ID", "AGE", "CITY"],
        "fields": {
            "ID": {"name": ["ID"], "type": "string", "identifiant": True},
            "AGE": {"name": ["AGE"], "type": "integer", "check_type": True},
            "CITY": {"name": ["CITY"], "type": "string"},
        },
    })
    # long, only ERROR
    lng = client.get(f"/api/files/{sid}/report", params={"shape": "long", "statuses": "ERROR"}).json()
    assert len(lng) == 2 and all(r["status"] == "ERROR" for r in lng)
    assert {r["ID"] for r in lng} == {"A2", "A3"}
    # by_id
    grp = client.get(f"/api/files/{sid}/report", params={"shape": "by_id", "statuses": "ERROR"}).json()
    assert {g["id"] for g in grp} == {"A2", "A3"}
    assert grp[0]["errors"][0]["column"] == "AGE"
    # pivot
    piv = client.get(f"/api/files/{sid}/report", params={"shape": "pivot", "statuses": "ERROR"}).json()
    assert any("AGE" in row for row in piv)
    # csv download
    csv = client.get(f"/api/files/{sid}/report", params={"shape": "long", "statuses": "ERROR", "fmt": "csv"})
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert "AGE" in csv.text


def test_tco_replace_mode():
    # Replace mode: M -> MASCULIN, F -> FEMININ ; X kept + flagged MAPPING_KO.
    sid = _upload("CIV\nM\nF\nX\n")["session_id"]
    client.post(f"/api/files/{sid}/tco",
                files={"file": ("tco.csv", io.BytesIO(TCO.encode()), "text/csv")},
                data={"source_col": "SOURCE_VALUE", "target_col": "TARGET_LABEL"})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CIV"],
        "fields": {"CIV": {"name": ["CIV"], "tco_replace": True}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    col = body["columns"].index("CIV")
    vals = [row[col] for row in body["data"]]
    assert vals[:2] == ["MASCULIN", "FEMININ"]      # replaced
    assert vals[2] == "X"                              # unknown kept
    # X surfaces as uncovered
    assert any(d["value"] == "X" for d in body["tco_uncovered"].get("CIV", []))


def test_tco_settings_roundtrip_in_config():
    exp = client.post("/api/config/export", json={
        "type": "CSV", "delimiter": ";", "header": {}, "visible_cols": ["A", "B"],
        "fields": {
            "A": {"name": ["A"], "tco_mapping": "MASCULIN"},
            "B": {"name": ["B"], "tco_replace": True, "check_type": True, "type": "integer",
                  "tco_type": "generic_job"},
        },
    }).json()
    assert "tco_mapping: MASCULIN" in exp["yaml"]
    assert "tco_replace: true" in exp["yaml"]
    assert "check_type: true" in exp["yaml"]
    assert "tco_type: generic_job" in exp["yaml"]
    imp = client.post("/api/config/import", json={"yaml": exp["yaml"]}).json()
    fa = next(f for f in imp["file_config"]["Fields"] if f["name"] == ["A"])
    fb = next(f for f in imp["file_config"]["Fields"] if f["name"] == ["B"])
    assert fa["tco_mapping"] == "MASCULIN"
    assert fb["tco_replace"] is True and fb["check_type"] is True
    assert fb["tco_type"] == "generic_job"


def test_filter_notnull_keywords():
    import json as _j
    sid = _upload("CODE;X\nA;1\n;2\nB;3\n;4\nC;5\n")["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CODE"], "fields": {"CODE": {"name": ["CODE"]}}})
    def n(f):
        return client.get(f"/api/files/{sid}/rows", params={"limit": 100, "filters": _j.dumps({"CODE": f})}).json()["total"]
    assert n("notnull") == 3 and n("not null") == 3 and n("!null") == 3   # non-empty
    assert n("null") == 2 and n("empty") == 2                              # empty


def test_filter_or_groups():
    import json as _j
    # A and B: at least one non-empty (group 1) ; C must equal Lyon (ungrouped)
    rows = "A;B;C\nx;;Lyon\n;y;Lyon\n;;Lyon\nx;y;Paris\n"
    sid = _upload(rows)["session_id"]
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["A", "B", "C"],
        "fields": {"A": {"name": ["A"]}, "B": {"name": ["B"]}, "C": {"name": ["C"]}},
    })
    def n(f):
        return client.get(f"/api/files/{sid}/rows", params={"limit": 100, "filters": _j.dumps(f)}).json()["total"]
    # group 1 OR: rows 1 (A=x), 2 (B=y), 4 (both) -> 3 ; row 3 excluded (both empty)
    assert n({"A": ":1!empty", "B": ":1!empty"}) == 3
    # combine with ungrouped C=Lyon: rows 1 and 2 (row 4 is Paris, row 3 both empty)
    assert n({"A": ":1!empty", "B": ":1!empty", "C": "=Lyon"}) == 2
    # without grouping (AND): A!empty AND B!empty -> only row 4
    assert n({"A": "!empty", "B": "!empty"}) == 1


def test_regex_charclass_and_trim():
    # The [0-9] character class inside the regex must survive preprocessing,
    # and LTRIM strips leading zeros dynamically.
    sid = _upload("CODE\n0012\n0122\n1002\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["CODE"],
        "fields": {"CODE": {"name": ["CODE"]}},
        "computed": [
            {"name": "rx", "expression": 'REGEX_EXTRACT([CODE], "0*([0-9]+)", 1)'},
            {"name": "lt", "expression": 'LTRIM([CODE], "0")'},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json(); c = out["columns"]
    assert [row[c.index("rx")] for row in out["data"]] == ["12", "122", "1002"]
    assert [row[c.index("lt")] for row in out["data"]] == ["12", "122", "1002"]


# ──────────────────────────────────────────────────────────────
# v11 — editable mode + empty-identifier regression
# ──────────────────────────────────────────────────────────────

def test_report_id_with_empty_identifier_value():
    # Regression: an EMPTY value in an identifier column used to crash /process
    # ("sequence item 0: expected str instance, float found") because astype(str)
    # on a StringDtype series keeps NaN as float. This is the samples/ demo case.
    sid = _upload("SIRET;NOM\n12345678901234;Alice\n;Bob\n")["session_id"]
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "NOM"],
        "fields": {"SIRET": {"name": ["SIRET"], "nullable": False, "identifiant": True},
                   "NOM": {"name": ["NOM"]}},
    })
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    nullable_rows = [x for x in rep if x["colonne"] == "SIRET" and x["statut"] == "ERROR"]
    assert len(nullable_rows) == 1
    assert nullable_rows[0]["id"] == ""          # empty identifier -> empty id, not a crash


def test_rows_and_process_return_index():
    sid = _upload("A;B\n1;x\n2;y\n3;z\n")["session_id"]
    up_idx = client.get(f"/api/files/{sid}/rows")   # before run -> 409, index comes from upload
    pr = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["A", "B"], "fields": {"A": {"name": ["A"]}, "B": {"name": ["B"]}}})
    assert pr.json()["index"] == [0, 1, 2]
    rows = client.get(f"/api/files/{sid}/rows", params={"limit": 2, "offset": 1}).json()
    assert rows["index"] == [1, 2] and len(rows["data"]) == 2


def test_edit_cell_then_process_clears_error():
    # '999' fails the SIRET regex; fix it by editing the cell, re-run, error gone.
    sid = _upload("SIRET\n12345678901234\n999\n")["session_id"]
    body = {"visible_cols": ["SIRET"],
            "fields": {"SIRET": {"name": ["SIRET"], "regex": r"^\d{14}$"}}}
    r1 = client.post(f"/api/files/{sid}/process", json=body).json()
    assert r1["stats"]["per_col"]["SIRET"]["errors"] == 1
    e = client.post(f"/api/files/{sid}/cells", json={
        "edits": [{"index": 1, "column": "SIRET", "value": "99999999999999"}]}).json()
    assert e["applied"] == 1 and e["stale"] is True and e["edits_total"] == 1
    r2 = client.post(f"/api/files/{sid}/process", json=body).json()
    assert r2["stats"]["per_col"]["SIRET"]["errors"] == 0
    assert r2["data"][1][0] == "99999999999999"


def test_edit_uses_source_column_name_when_renamed():
    # Display shows the renamed column; edits address the SOURCE name in work_df.
    sid = _upload("OLD\nfoo\nbar\n")["session_id"]
    body = {"visible_cols": ["OLD"],
            "fields": {"OLD": {"name": ["OLD"], "mapping": "NEW", "on_list": ["foo", "baz"]}}}
    r1 = client.post(f"/api/files/{sid}/process", json=body).json()
    assert "NEW" in r1["columns"]
    assert r1["stats"]["per_col"]["NEW"]["errors"] == 1          # 'bar' not on list
    ok = client.post(f"/api/files/{sid}/cells", json={
        "edits": [{"index": 1, "column": "OLD", "value": "baz"}]}).json()
    assert ok["applied"] == 1
    r2 = client.post(f"/api/files/{sid}/process", json=body).json()
    assert r2["stats"]["per_col"]["NEW"]["errors"] == 0


def test_edit_rejects_unknown_row_or_column():
    sid = _upload("A\n1\n")["session_id"]
    e = client.post(f"/api/files/{sid}/cells", json={"edits": [
        {"index": 0, "column": "A", "value": "2"},
        {"index": 99, "column": "A", "value": "x"},
        {"index": 0, "column": "NOPE", "value": "x"},
    ]}).json()
    assert e["applied"] == 1
    reasons = sorted(r["reason"] for r in e["rejected"])
    assert reasons == ["unknown column", "unknown row"]


def test_edit_reset_restores_values_and_header():
    raw = "\n\nA;B\n1;x\n2;y\n"                     # empty lines before the header
    sid = _upload(raw)["session_id"]
    client.post(f"/api/files/{sid}/header", json={"header": {
        "auto_header": True, "delete_all_empty_line": True}})
    client.post(f"/api/files/{sid}/cells", json={
        "edits": [{"index": 0, "column": "A", "value": "EDITED"}]})
    tp = client.post(f"/api/files/{sid}/cells/reset").json()
    assert tp["data"][0][0] == "1"                  # value restored
    assert tp["columns"] == ["A", "B"]              # header treatment re-applied
    assert tp["index"] == [0, 1]


def test_edit_before_first_run_is_used_by_process():
    sid = _upload("A\nold\n")["session_id"]
    client.post(f"/api/files/{sid}/cells", json={
        "edits": [{"index": 0, "column": "A", "value": "new"}]})
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["A"], "fields": {"A": {"name": ["A"]}}}).json()
    assert r["data"][0][0] == "new"
