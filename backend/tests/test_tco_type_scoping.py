"""
A TCO shared by several fields can have two TYPEs whose codes collide by
coincidence (a job title and a legal-structure label both coded "01", say) —
a field naming its own `tco_type` must only ever resolve against its slice,
never silently pick up the other type's row.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# Same SOURCE_VALUE ("01") means something different per TYPE.
TCO_COLLIDING = (
    "TYPE;SOURCE_VALUE;TARGET_LABEL\n"
    "generic_job;01;INGENIEUR\n"
    "legal_structure;01;SARL\n"
)

CSV = "POSTE;STRUCTURE\n01;01\n"


def _upload_with_tco():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    client.post(f"/api/files/{sid}/tco",
               files={"file": ("tco.csv", io.BytesIO(TCO_COLLIDING.encode()), "text/csv")},
               data={"delimiter": ";"})
    return sid


def test_replace_mode_resolves_within_its_own_type_despite_a_colliding_code():
    sid = _upload_with_tco()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["POSTE", "STRUCTURE"],
        "fields": {
            "POSTE": {"name": ["POSTE"], "tco_replace": True, "tco_type": "generic_job"},
            "STRUCTURE": {"name": ["STRUCTURE"], "tco_replace": True, "tco_type": "legal_structure"},
        },
    })
    out = r.json()
    assert out["data"][0] == ["INGENIEUR", "SARL"], out


def test_without_a_declared_type_the_whole_table_applies_as_before():
    """Backward compatibility: a field that names no `tco_type` still sees the
    whole table — existing single-purpose TCOs must behave exactly as before
    this existed."""
    sid = _upload_with_tco()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["POSTE"],
        "fields": {"POSTE": {"name": ["POSTE"], "tco_replace": True}},
    })
    out = r.json()
    # Unscoped: whichever row for SOURCE_VALUE "01" the flat map lands on last.
    assert out["data"][0][0] in ("INGENIEUR", "SARL")
    assert out["status"][0][0] == "MAPPING_OK"


def test_validate_mode_is_also_scoped_by_type():
    sid = _upload_with_tco()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["POSTE"],
        "fields": {"POSTE": {"name": ["POSTE"], "tco_mapping": "INGENIEUR", "tco_type": "generic_job"}},
    })
    assert r.json()["status"][0][0] == "MAPPING_OK"

    r2 = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["POSTE"],
        "fields": {"POSTE": {"name": ["POSTE"], "tco_mapping": "SARL", "tco_type": "generic_job"}},
    })
    # "01" under generic_job maps to INGENIEUR, not SARL — scoping must refuse it
    assert r2.json()["status"][0][0] == "MAPPING_KO"
