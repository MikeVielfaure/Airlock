"""
The mapping artefact and the central pivot: every source translates to and from
one canonical shape, so a conversion is always source → pivot → target. These
tests drive the three promised bricks end to end — a stored, reversible mapping;
the "turn any source into a pivot object" button; and anything↔anything through
the hub.
"""
import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.mapping_models import mapping_from_yaml
from app.services import pivot_service

client = TestClient(app)

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "edi"
ORDERS = (SAMPLES / "orders_d96a.edi").read_text()
MODEL = (SAMPLES / "model_orders_d96a.yaml").read_text()


def edi_file():
    return {"file": ("commande", ORDERS.encode(), "application/octet-stream")}


# A mapping that ties a partner CSV's own column names to canonical pivot fields.
PARTNER_MAP = """
name: partenaire ↔ pivot
source_kind: flat
links:
  - {pivot: numero_commande, source: NoCmd, scope: head}
  - {pivot: acheteur_id,     source: Client, scope: head}
  - {pivot: code_article,    source: EAN,    scope: item}
  - {pivot: quantite,        source: Qte,    scope: item}
"""

PARTNER_CSV = (
    "NoCmd;Client;EAN;Qte\n"
    "PO900;CLI1;3011111111111;5\n"
    "PO900;CLI1;3022222222222;8\n"
)


# ══════════════════════════════════════════════════════════════════════
# Brick 1 — a mapping is a versioned, reversible artefact
# ══════════════════════════════════════════════════════════════════════
def test_a_mapping_is_stored_and_versioned_like_any_artefact():
    r = client.post("/api/artefacts/mapping",
                    json={"name": "map-A", "yaml": PARTNER_MAP})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert aid in [a["id"] for a in client.get("/api/artefacts/mapping").json()]

    v2 = client.post(f"/api/artefacts/mapping/{aid}/versions",
                     json={"yaml": PARTNER_MAP})
    assert v2.status_code in (200, 201)


def test_an_invalid_mapping_is_refused_before_storage():
    bad = "name: x\nlinks:\n  - {pivot: a, source: b, scope: head}\n  - {pivot: a, source: c, scope: head}\n"
    r = client.post("/api/artefacts/mapping", json={"name": "dup", "yaml": bad})
    assert r.status_code == 422                       # duplicate pivot in one scope


def test_a_mapping_round_trips_flat_to_pivot_to_flat():
    m = mapping_from_yaml(PARTNER_MAP)
    import pandas as pd
    df = pd.read_csv(io.StringIO(PARTNER_CSV), sep=";", dtype=str)
    records = pivot_service.flat_to_pivot(df, m)
    assert len(records) == 1                          # one document (NoCmd constant)
    assert len(records[0]["items"]) == 2
    assert records[0]["head"]["numero_commande"] == "PO900"

    back = pivot_service.pivot_to_flat(records, m)
    assert set(back.columns) == {"NoCmd", "Client", "EAN", "Qte"}
    assert list(back["EAN"]) == ["3011111111111", "3022222222222"]


# ══════════════════════════════════════════════════════════════════════
# Brick 2 — the universal "turn into a pivot object" button
# ══════════════════════════════════════════════════════════════════════
def test_a_flat_upload_becomes_a_pivot_object():
    r = client.post("/api/pivot/from/flat",
                    files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"mapping_yaml": PARTNER_MAP, "target": "preview"})
    assert r.status_code == 200
    body = r.json()
    assert body["documents"] == 1
    assert "numero_commande" in body["preview"]["columns"]


def test_an_edi_file_becomes_a_pivot_object():
    # identity mapping suggested from the model, EDI side
    sug = client.post("/api/mapping/suggest",
                      data={"source_kind": "edi", "edi_model_yaml": MODEL})
    assert sug.status_code == 200
    r = client.post("/api/pivot/from/edi", files=edi_file(),
                    data={"edi_model_yaml": MODEL, "mapping_yaml": sug.json()["yaml"],
                          "target": "preview"})
    assert r.status_code == 200
    assert r.json()["documents"] == 2                 # two messages in the sample
    cols = r.json()["preview"]["columns"]
    assert "numero_commande" in cols and "code_article" in cols


def test_pivot_object_target_session_feeds_the_cleaning_pipeline():
    r = client.post("/api/pivot/from/flat",
                    files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"mapping_yaml": PARTNER_MAP, "target": "session"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    # it is now an ordinary session: the pipeline runs on it
    run = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["numero_commande", "code_article", "quantite"],
        "fields": {"numero_commande": {"name": ["numero_commande"], "type": "string"},
                   "code_article": {"name": ["code_article"], "type": "string",
                                    "regex": r"^\d{13}$"},
                   "quantite": {"name": ["quantite"], "type": "integer"}}})
    assert run.status_code == 200
    assert run.json()["stats"]["total_rows"] == 2


def test_mapping_suggestion_from_a_flat_file_splits_head_and_items():
    r = client.post("/api/mapping/suggest",
                    files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"source_kind": "flat"})
    m = mapping_from_yaml(r.json()["yaml"])
    heads = {l.source for l in m.head_links()}
    items = {l.source for l in m.item_links()}
    assert "NoCmd" in heads and "Client" in heads      # constant columns → head
    assert "EAN" in items and "Qte" in items           # varying columns → item


# ══════════════════════════════════════════════════════════════════════
# Brick 3 — anything ↔ anything through the pivot
# ══════════════════════════════════════════════════════════════════════
EDI_MAP = """
name: ORDERS ↔ pivot
source_kind: edi
links:
  - {pivot: numero_commande, source: numero_commande, scope: head}
  - {pivot: acheteur_id,     source: acheteur_id,     scope: head}
  - {pivot: type_document,   source: type_document,   scope: head}
  - {pivot: code_article,    source: code_article,    scope: item}
  - {pivot: quantite,        source: quantite_commandee, scope: item}
"""


def test_flat_to_edi_through_the_pivot():
    """A partner CSV becomes a valid EDIFACT ORDERS, its columns bridged to EDI
    fields entirely through the canonical pivot."""
    r = client.post("/api/pivot/convert", files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"source_kind": "flat", "target_kind": "edi",
                          "source_mapping_yaml": PARTNER_MAP,
                          "target_mapping_yaml": EDI_MAP,
                          "target_edi_model_yaml": MODEL,
                          "sender": "A:14", "recipient": "B:14",
                          "interchange_ref": "R1"})
    assert r.status_code == 200, r.text
    edi = r.json()["preview"]
    assert "UNH+" in edi and "LIN+" in edi
    assert "3011111111111" in edi                      # the EAN travelled across

    # the produced EDI validates against the model
    v = client.post("/api/edi/validate",
                    files={"file": ("x", edi.encode(), "application/octet-stream")},
                    data={"model_yaml": MODEL})
    assert v.json()["ok"] is True, v.json()


def test_edi_to_flat_through_the_pivot():
    r = client.post("/api/pivot/convert", files=edi_file(),
                    data={"source_kind": "edi", "target_kind": "flat",
                          "source_edi_model_yaml": MODEL,
                          "source_mapping_yaml": EDI_MAP,
                          "target_mapping_yaml": PARTNER_MAP})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "csv"
    cols = body["preview"]["columns"]
    assert {"NoCmd", "EAN", "Qte"} <= set(cols)        # renamed to partner columns
    # the first order's EANs are present
    dump = str(body["preview"]["data"])
    assert "3456789012345" in dump


def test_edi_to_edi_still_works_through_the_generic_path():
    r = client.post("/api/pivot/convert", files=edi_file(),
                    data={"source_kind": "edi", "target_kind": "edi",
                          "source_edi_model_yaml": MODEL,
                          "source_mapping_yaml": EDI_MAP,
                          "target_mapping_yaml": EDI_MAP,
                          "target_edi_model_yaml": MODEL,
                          "sender": "A:14", "recipient": "B:14", "interchange_ref": "R2"})
    assert r.status_code == 200, r.text
    v = client.post("/api/edi/validate",
                    files={"file": ("x", r.json()["preview"].encode(), "application/octet-stream")},
                    data={"model_yaml": MODEL})
    assert v.json()["ok"] is True


def test_a_stored_mapping_drives_a_conversion():
    aid = client.post("/api/artefacts/mapping",
                      json={"name": "stored-partner", "yaml": PARTNER_MAP}).json()["id"]
    mid = client.post("/api/artefacts/mapping",
                      json={"name": "stored-edi", "yaml": EDI_MAP}).json()["id"]
    em = client.post("/api/artefacts/edi_model",
                     json={"name": "stored-model", "yaml": MODEL}).json()["id"]

    r = client.post("/api/pivot/convert", files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"source_kind": "flat", "target_kind": "edi",
                          "source_mapping_id": aid, "target_mapping_id": mid,
                          "target_edi_model_id": em,
                          "sender": "A:14", "recipient": "B:14", "interchange_ref": "R3"})
    assert r.status_code == 200, r.text
    assert "UNH+" in r.json()["preview"]


# ══════════════════════════════════════════════════════════════════════
# Unification: a field has one origin (source OR expression) and constraints
# ══════════════════════════════════════════════════════════════════════
SMART_MAP = """
name: partenaire enrichi
source_kind: flat
links:
  - {pivot: numero_commande, source: NoCmd, scope: head}
  - pivot: reference
    expr: "CONCAT([NoCmd], '-', [Client])"
    scope: head
  - {pivot: code_article, source: EAN, scope: item, rules: {type: string, regex: "^\\\\d{13}$"}}
  - pivot: quantite
    source: Qte
    scope: item
    rules: {type: integer}
"""


def test_a_link_can_compute_its_value_instead_of_reading_it():
    r = client.post("/api/pivot/from/flat",
                    files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"mapping_yaml": SMART_MAP, "target": "preview"})
    assert r.status_code == 200, r.text
    body = r.json()
    cols = body["preview"]["columns"]
    assert "reference" in cols                      # produced by the expression
    dump = str(body["preview"]["data"])
    assert "PO900-CLI1" in dump                     # CONCAT actually ran


def test_a_link_cannot_have_both_a_source_and_an_expression():
    bad = ("name: x\nlinks:\n"
           "  - {pivot: a, source: b, expr: \"CONCAT(b,'x')\", scope: head}\n")
    r = client.post("/api/artefacts/mapping", json={"name": "both", "yaml": bad})
    assert r.status_code == 422
    assert "source" in r.json()["detail"].lower()


def test_constraints_are_checked_by_the_same_engine_as_the_pipeline():
    ok = client.post("/api/pivot/from/flat",
                     files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                     data={"mapping_yaml": SMART_MAP, "target": "preview"}).json()
    assert ok["checks"]["ok"] is True, ok["checks"]

    bad_csv = "NoCmd;Client;EAN;Qte\nPO901;CLI2;123;5\n"      # EAN too short
    ko = client.post("/api/pivot/from/flat",
                     files={"file": ("p.csv", bad_csv.encode(), "text/csv")},
                     data={"mapping_yaml": SMART_MAP, "target": "preview"}).json()
    assert ko["checks"]["ok"] is False
    assert any(p["field"] == "code_article" for p in ko["checks"]["problems"])


def test_a_broken_expression_is_reported_not_a_500():
    broken = ("name: x\nsource_kind: flat\nlinks:\n"
              "  - {pivot: a, source: NoCmd, scope: head}\n"
              "  - {pivot: b, expr: \"NOPE(\", scope: head}\n")
    r = client.post("/api/pivot/from/flat",
                    files={"file": ("p.csv", PARTNER_CSV.encode(), "text/csv")},
                    data={"mapping_yaml": broken, "target": "preview"})
    assert r.status_code == 200                     # degraded, not crashed
