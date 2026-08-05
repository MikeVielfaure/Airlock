"""
End-to-end tests for the EDI module: format detection, the generic lexer
(UNA-driven separators, escaping), free syntax checks (envelope counters and
references), model-driven validation, both pivot modes, the generator with its
golden round-trip, EDI→EDI conversion, model inference, and edi_model artefacts
in the versioned library.
"""

import os
from pathlib import Path

os.environ.setdefault("FX_MASTER_KEY", "cle-maitresse-de-test-pour-la-suite")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.edi_models import model_from_yaml
from app.services import edi_lexer as lexer
from app.services import edi_service as edi

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_users_and_keys():
    """Every other test in this file relies on setup-mode (no account yet,
    so /api/files et al. work unauthenticated) — a lingering account from
    the confidentiality test would break all of them. Same pattern as
    test_crypto.py/test_diff.py."""
    from app.db import session_scope
    from app.db_models import CryptoKey, KeyHolder, Membership, RevealEvent, User
    def wipe():
        with session_scope() as s:
            for m in (RevealEvent, KeyHolder, CryptoKey, Membership, User):
                for row in s.query(m).all():
                    s.delete(row)
            s.commit()
    wipe(); yield; wipe()


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "edi"
ORDERS = (SAMPLES / "orders_d96a.edi").read_text(encoding="utf-8")
DESADV = (SAMPLES / "desadv_d96a.edi").read_text(encoding="utf-8")
M_ORDERS = (SAMPLES / "model_orders_d96a.yaml").read_text(encoding="utf-8")
M_DESADV = (SAMPLES / "model_desadv_d96a.yaml").read_text(encoding="utf-8")


def upload(text: str, name: str = "commande"):
    """EDI files usually have no extension — that is the point."""
    return {"file": (name, text.encode("utf-8"), "application/octet-stream")}


# ══════════════════════════════════════════════════════════════════════
# Detection & lexer
# ══════════════════════════════════════════════════════════════════════
def test_detect_edifact_with_and_without_una():
    assert lexer.detect_format(ORDERS) == "EDIFACT"          # starts with UNA
    assert lexer.detect_format(DESADV) == "EDIFACT"          # starts with UNB
    assert lexer.detect_format("nom;prenom\nAda;Lovelace") == "UNKNOWN"


def test_detect_x12_is_reported_not_parsed():
    isa = "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260715*1200*U*00401*000000001*0*P*>~"
    assert len(isa) >= 106
    assert lexer.detect_format(isa) == "X12"
    r = client.post("/api/edi/inspect", files=upload(isa))
    assert r.status_code == 422
    assert "X12" in r.json()["detail"]


def test_lexer_reads_custom_una_separators_and_escaping():
    text = "UNA=*.? ~UNB*UNOA=2*ME*YOU*260715=1200*R1~FTX*AAA*L?*Escape=me?~ok~UNZ*0*R1~"
    seps, segs, had_una = lexer.parse(text)
    assert had_una and seps.component == "=" and seps.element == "*" and seps.segment == "~"
    ftx = [s for s in segs if s.tag == "FTX"][0]
    # `?*`, `?~` are released: they stay data, they do not split anything
    assert lexer.get(ftx, "2.1") == "L*Escape"
    assert lexer.get(ftx, "2.2") == "me~ok"


def test_lexer_round_trip_preserves_content():
    seps, segs, _ = lexer.parse(ORDERS)
    rendered = lexer.render([(s.tag, s.elements) for s in segs], seps, una=True)
    _seps2, segs2, _ = lexer.parse(rendered)
    assert [(s.tag, s.elements) for s in segs] == [(s.tag, s.elements) for s in segs2]


# ══════════════════════════════════════════════════════════════════════
# Inspect: decoded tree + free syntax checks
# ══════════════════════════════════════════════════════════════════════
def test_inspect_decodes_the_tree_with_plain_language_labels():
    r = client.post("/api/edi/inspect", files=upload(ORDERS))
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "EDIFACT" and body["had_una"] is True
    assert body["syntax_errors"] == []
    assert len(body["interchanges"]) == 1
    inter = body["interchanges"][0]
    assert inter["ref"] == "IC260715001"
    assert len(inter["messages"]) == 2
    assert inter["messages"][0]["type"] == "ORDERS"
    dump = str(body)
    assert "Acheteur" in dump                     # NAD+BY decoded from the KB
    assert "Nom et adresse" in dump               # segment label


def test_broken_unt_counter_is_caught():
    r = client.post("/api/edi/inspect", files=upload(ORDERS.replace("UNT+14+", "UNT+99+")))
    codes = [e["code"] for e in r.json()["syntax_errors"]]
    assert "COMPTEUR_UNT" in codes


def test_broken_unz_counter_and_missing_unz_are_caught():
    r = client.post("/api/edi/inspect", files=upload(ORDERS.replace("UNZ+2+", "UNZ+5+")))
    assert "COMPTEUR_UNZ" in [e["code"] for e in r.json()["syntax_errors"]]

    r = client.post("/api/edi/inspect", files=upload(ORDERS.replace("UNZ+2+IC260715001'\n", "")))
    assert "UNZ_MANQUANT" in [e["code"] for e in r.json()["syntax_errors"]]


def test_mismatched_interchange_reference_is_caught():
    r = client.post("/api/edi/inspect", files=upload(ORDERS.replace("UNZ+2+IC260715001", "UNZ+2+IC999")))
    assert "REF_INTERCHANGE" in [e["code"] for e in r.json()["syntax_errors"]]


# ══════════════════════════════════════════════════════════════════════
# Model-driven validation
# ══════════════════════════════════════════════════════════════════════
def test_validate_clean_file_against_its_model():
    r = client.post("/api/edi/validate", files=upload(ORDERS), data={"model_yaml": M_ORDERS})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["stats"] == {"messages": 2, "items": 3, "errors": 0}


def test_shipped_samples_validate_against_shipped_models():
    r = client.post("/api/edi/validate", files=upload(DESADV), data={"model_yaml": M_DESADV})
    assert r.json()["ok"] is True, r.json()["model_errors"]


def test_missing_required_variant_is_reported():
    r = client.post("/api/edi/validate",
                    files=upload(ORDERS.replace("NAD+BY+5412345000013::9'\n", "", 1)),
                    data={"model_yaml": M_ORDERS})
    body = r.json()
    assert body["ok"] is False
    assert "VARIANTE_MANQUANTE" in [e["code"] for e in body["model_errors"]]


def test_forbidden_code_invalid_number_and_invalid_date():
    bad = (ORDERS.replace("BGM+220+PO12345", "BGM+999+PO12345")
                 .replace("QTY+21:48", "QTY+21:quarante-huit")
                 .replace("DTM+137:20260715:102", "DTM+137:2026071:102"))
    r = client.post("/api/edi/validate", files=upload(bad), data={"model_yaml": M_ORDERS})
    codes = [e["code"] for e in r.json()["model_errors"]]
    assert "CODE_INTERDIT" in codes
    assert "NOMBRE_INVALIDE" in codes
    assert "DATE_INVALIDE" in codes


def test_unexpected_segment_and_wrong_message_type():
    r = client.post("/api/edi/validate",
                    files=upload(ORDERS.replace("UNS+S'", "FOO+1'\nUNS+S'")),
                    data={"model_yaml": M_ORDERS})
    assert "SEGMENT_INATTENDU" in [e["code"] for e in r.json()["model_errors"]]

    r = client.post("/api/edi/validate", files=upload(DESADV), data={"model_yaml": M_ORDERS})
    assert "TYPE_MESSAGE" in [e["code"] for e in r.json()["model_errors"]]


# ══════════════════════════════════════════════════════════════════════
# Pivot
# ══════════════════════════════════════════════════════════════════════
def test_pivot_flat_repeats_the_head_on_every_item_line():
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "preview"})
    assert r.status_code == 200
    flat = r.json()["flat"]
    cols, rows = flat["columns"], flat["data"]
    assert len(rows) == 3                                   # 2 + 1 items
    order_no = [row[cols.index("numero_commande")] for row in rows]
    assert order_no == ["PO12345", "PO12345", "PO12346"]    # head repeated
    assert rows[0][cols.index("quantite_commandee")] == "48"


def test_pivot_linked_splits_heads_and_items():
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "linked", "target": "preview"})
    body = r.json()
    assert body["mode"] == "linked"
    assert len(body["heads"]["data"]) == 2
    assert len(body["items"]["data"]) == 3
    assert "message_no" in body["heads"]["columns"]          # the join key
    assert "message_no" in body["items"]["columns"]


def test_pivot_into_a_data_session_hands_over_to_the_existing_pipeline():
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "session"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]
    assert len(body["preview"]["data"]) == 3
    # the pivoted table is now an ordinary session: the existing validation
    # pipeline runs on it unchanged — an EAN that is not 13 digits is flagged
    sid = body["session_id"]
    run = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["numero_commande", "code_article", "quantite_commandee"],
        "fields": {
            "numero_commande": {"type": "string", "nullable": False, "identifiant": True},
            "code_article": {"type": "string", "regex": r"^\\d{13}$", "nullable": False},
            "quantite_commandee": {"type": "integer", "nullable": False},
        }})
    assert run.status_code == 200, run.text
    out = run.json()
    assert len(out["data"]) == 3
    assert set(out["columns"]) >= {"numero_commande", "code_article", "quantite_commandee"}
    assert out["stats"]["total_rows"] == 3


def test_pivot_to_csv_returns_a_download():
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "csv"})
    files = r.json()["files"]
    assert len(files) == 1 and files[0]["filename"].endswith(".csv")


# ── extract_records is best-effort: a segment the model doesn't expect is
# dropped rather than blocking the pivot. Silent is the problem this closes —
# every pivot target now also carries what validate() would have flagged. ──

def test_pivot_reports_clean_stats_when_the_file_matches_its_model():
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "preview"})
    body = r.json()
    assert body["model_errors"] == []
    assert body["model_stats"] == {"messages": 2, "items": 3, "errors": 0}


def test_pivot_surfaces_a_deviation_it_silently_tolerated():
    """The pivot itself must still succeed — extraction is best-effort — but
    the caller now learns a segment was outside the model, instead of the
    data simply being missing with no explanation."""
    r = client.post("/api/edi/pivot",
                    files=upload(ORDERS.replace("UNS+S'", "FOO+1'\nUNS+S'")),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "preview"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["flat"]["data"]) == 3          # still pivoted normally
    assert "SEGMENT_INATTENDU" in [e["code"] for e in body["model_errors"]]


def test_pivot_to_session_also_carries_model_errors():
    r = client.post("/api/edi/pivot",
                    files=upload(ORDERS.replace("UNS+S'", "FOO+1'\nUNS+S'")),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "session"})
    body = r.json()
    assert body["session_id"]
    assert "SEGMENT_INATTENDU" in [e["code"] for e in body["model_errors"]]


# ══════════════════════════════════════════════════════════════════════
# Generation — the golden round-trip
# ══════════════════════════════════════════════════════════════════════
def test_golden_round_trip_edi_to_flat_to_edi():
    """parse(generate(pivot(parse(x)))) == parse(x) — records must survive."""
    model = model_from_yaml(M_ORDERS)
    seps, segs, _ = lexer.parse(ORDERS)
    inters, _ = edi.split_interchanges(segs)
    source_records = edi.extract_records(inters, model)

    flat = edi.pivot(source_records, model, "flat")["flat"]
    csv_bytes = flat.to_csv(sep=";", index=False).encode("utf-8-sig")

    r = client.post("/api/edi/generate",
                    files={"file": ("pivot.csv", csv_bytes, "text/csv")},
                    data={"model_yaml": M_ORDERS, "sender": "5498765000021:14",
                          "recipient": "5412345000013:14", "interchange_ref": "IC260715001"})
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == 2 and body["items"] == 3
    rebuilt = body["preview"]

    # the regenerated file is valid on its own terms…
    v = client.post("/api/edi/validate", files=upload(rebuilt), data={"model_yaml": M_ORDERS})
    assert v.json()["ok"] is True, v.json()

    # …and carries exactly the same data
    seps2, segs2, _ = lexer.parse(rebuilt)
    inters2, syntax_errors = edi.split_interchanges(segs2)
    assert syntax_errors == []
    for before, after in zip(source_records, edi.extract_records(inters2, model)):
        assert before["head"] == after["head"]
        assert before["items"] == after["items"]


def test_generated_file_carries_summary_segments_and_counters():
    model = model_from_yaml(M_ORDERS)
    seps, segs, _ = lexer.parse(ORDERS)
    inters, _ = edi.split_interchanges(segs)
    records = edi.extract_records(inters, model)
    text = edi.generate(model, records, sender="A:14", recipient="B:14", interchange_ref="R1")
    assert "UNS+S'" in text
    assert "CNT+2:2'" in text            # 2 items on the first message
    assert "UNT+14+M000001'" in text     # counter recomputed, matches the source
    assert "UNZ+2+R1'" in text


def test_generate_rejects_a_table_with_no_known_column():
    r = client.post("/api/edi/generate",
                    files={"file": ("x.csv", b"a;b\n1;2\n", "text/csv")},
                    data={"model_yaml": M_ORDERS})
    assert r.status_code == 422
    assert "colonne" in r.json()["detail"].lower()


# ══════════════════════════════════════════════════════════════════════
# Generating from an already-cleaned session — the trip /api/edi/generate
# couldn't make: a messy file, pivoted into a session and cleaned through
# the normal field-rule engine, has to be able to come back out as EDI.
# ══════════════════════════════════════════════════════════════════════
def test_generate_edi_from_a_session_cleaned_by_the_normal_pipeline():
    sid = client.post("/api/edi/pivot", files=upload(ORDERS),
                      data={"model_yaml": M_ORDERS, "mode": "flat", "target": "session"}
                      ).json()["session_id"]
    run = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["numero_commande", "code_article", "quantite_commandee"],
        "fields": {
            "numero_commande": {"type": "string", "nullable": False, "identifiant": True},
            "code_article": {"type": "string", "regex": r"^\\d{13}$", "nullable": False},
            "quantite_commandee": {"type": "integer", "nullable": False},
        }})
    assert run.status_code == 200, run.text

    r = client.post(f"/api/files/{sid}/edi/generate", data={"model_yaml": M_ORDERS})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["messages"] == 2 and body["items"] == 3
    v = client.post("/api/edi/validate", files=upload(body["preview"]), data={"model_yaml": M_ORDERS})
    assert v.json()["ok"] is True, v.json()


def test_generate_from_session_rejects_when_no_column_matches_the_model():
    sid = client.post("/api/files", files={"file": ("d.csv", b"a;b\n1;2\n", "text/csv")},
                      data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"}
                      ).json()["session_id"]
    r = client.post(f"/api/files/{sid}/edi/generate", data={"model_yaml": M_ORDERS})
    assert r.status_code == 422
    assert "colonne" in r.json()["detail"].lower()


def test_generate_from_session_masks_confidential_columns():
    """A column masked on screen must not leave in the clear the moment the
    session is turned into an EDI file — same rule as CSV/XLSX export."""
    client.post("/api/auth/signup", json={"email": "edikey@x.fr", "password": "motdepasse1"})
    token = client.post("/api/auth/login",
                        json={"email": "edikey@x.fr", "password": "motdepasse1"}).json()["token"]
    H = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/keys", json={"name": "commandes"}, headers=H).status_code == 200

    sid = client.post("/api/edi/pivot", files=upload(ORDERS),
                      data={"model_yaml": M_ORDERS, "mode": "flat", "target": "session"},
                      headers=H).json()["session_id"]
    run = client.post(f"/api/files/{sid}/process", headers=H, json={
        "visible_cols": ["numero_commande", "code_article", "quantite_commandee"],
        "fields": {
            "numero_commande": {"type": "string", "nullable": False, "identifiant": True,
                                "sensitive": "commandes"},
            "code_article": {"type": "string", "regex": r"^\\d{13}$", "nullable": False},
            "quantite_commandee": {"type": "integer", "nullable": False},
        }})
    assert run.status_code == 200, run.text
    assert run.json()["data"][0][0] == "•••••"

    r = client.post(f"/api/files/{sid}/edi/generate", data={"model_yaml": M_ORDERS}, headers=H)
    assert r.status_code == 200, r.text
    assert "PO12345" not in r.text and "PO12346" not in r.text
    assert "•••••" in r.json()["preview"]


# ══════════════════════════════════════════════════════════════════════
# EDI -> EDI conversion, through the internal pivot
# ══════════════════════════════════════════════════════════════════════
PARTNER_MODEL = """
name: ORDERS partenaire
message_type: ORDERS
directory: D96A
header:
  - tag: BGM
    status: M
    fields:
      - {name: type_document, path: "1.1"}
      - {name: ref_commande, path: "2.1"}
  - tag: RFF
    status: M
    max: 3
    qualifier: "1.1"
    when:
      "ON":
        label: Numéro de commande
        required: true
        fields:
          - {name: rff_commande, path: "1.2"}
  - tag: NAD
    status: M
    max: 5
    qualifier: "1.1"
    when:
      "BY":
        label: Acheteur
        required: true
        fields:
          - {name: acheteur_gln, path: "2.1"}
items:
  loop_start: LIN
  segments:
    - tag: LIN
      status: M
      fields:
        - {name: ligne, path: "1.1"}
        - {name: ean, path: "3.1"}
    - tag: QTY
      status: M
      qualifier: "1.1"
      when:
        "21":
          required: true
          fields:
            - {name: qte, path: "1.2", type: number}
summary: []
"""


def test_convert_between_two_models_with_a_field_mapping():
    mapping = ('{"ref_commande": "numero_commande", "rff_commande": "numero_commande",'
               ' "acheteur_gln": "acheteur_id", "ligne": "numero_ligne",'
               ' "ean": "code_article", "qte": "quantite_commandee"}')
    r = client.post("/api/edi/convert", files=upload(ORDERS),
                    data={"source_yaml": M_ORDERS, "target_yaml": PARTNER_MODEL,
                          "mapping": mapping, "sender": "A:14", "recipient": "B:14"})
    assert r.status_code == 200
    out = r.json()["preview"]
    assert "RFF+ON:PO12345'" in out          # target-only segment, fed by the mapping
    assert "LIN+1++3456789012345'" in out
    # the produced file validates against the *target* model
    v = client.post("/api/edi/validate", files=upload(out), data={"model_yaml": PARTNER_MODEL})
    assert v.json()["ok"] is True, v.json()


# ══════════════════════════════════════════════════════════════════════
# Model inference from a sample file
# ══════════════════════════════════════════════════════════════════════
def test_infer_a_model_that_actually_validates_its_own_sample():
    r = client.post("/api/edi/models/infer", files=upload(ORDERS),
                    data={"name": "ORDERS inféré"})
    assert r.status_code == 200
    body = r.json()
    inferred = model_from_yaml(body["yaml"])            # the YAML is well-formed
    assert inferred.message_type == "ORDERS"
    assert inferred.directory == "D96A"
    assert inferred.items.loop_start == "LIN"
    assert body["notes"]                                 # human-readable caveats

    v = client.post("/api/edi/validate", files=upload(ORDERS),
                    data={"model_yaml": body["yaml"]})
    assert v.json()["ok"] is True, v.json()["model_errors"]


def test_knowledge_base_feeds_the_in_app_documentation():
    kb = client.get("/api/edi/kb").json()
    assert "segments" in kb and "qualifiers" in kb and "separators" in kb
    nad = [s for s in kb["segments"] if s["tag"] == "NAD"][0]
    assert nad["name"] and nad["elements"]
    assert any(s["role"] == "release" for s in kb["separators"])
    assert any(q["tag"] == "NAD" and q["code"] == "BY" for q in kb["qualifiers"])


# ══════════════════════════════════════════════════════════════════════
# edi_model as a versioned artefact in the library
# ══════════════════════════════════════════════════════════════════════
def test_edi_model_lives_in_the_versioned_library():
    r = client.post("/api/artefacts/edi_model",
                    json={"name": "ORDERS partenaire X", "yaml": M_ORDERS})
    assert r.status_code == 201, r.text
    art = r.json()
    aid = art["id"]

    assert client.get("/api/artefacts/edi_model").status_code == 200
    assert aid in [a["id"] for a in client.get("/api/artefacts/edi_model").json()]

    y = client.get(f"/api/edi/models/{aid}/versions/1/yaml")
    assert y.status_code == 200
    assert model_from_yaml(y.json()["yaml"]).message_type == "ORDERS"

    # a model stored in the library drives a pivot, pinned to its version
    p = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_id": aid, "model_version": 1,
                          "mode": "flat", "target": "preview"})
    assert p.status_code == 200
    assert len(p.json()["flat"]["data"]) == 3


def test_invalid_edi_model_is_refused_before_storage():
    r = client.post("/api/artefacts/edi_model",
                    json={"name": "cassé", "yaml": "name: x\nmessage_type: ORDERS\nheader:\n  - tag: TOOLONG\n"})
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════
# Access control — found live, testing this module's own logic: every
# operational route (inspect, validate, pivot, generate, convert,
# models/infer) was reachable with zero auth, regardless of whether an
# account existed on the install — the one thing CLAUDE.md's own "une
# connexion obligatoire, pas d'entrée libre" is supposed to rule out
# everywhere else. All existing tests above still pass unauthenticated
# because they run in setup mode (no account exists — the autouse fixture
# wipes User each test); these create real accounts to exercise the gate.
# ══════════════════════════════════════════════════════════════════════
def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_operational_routes_refuse_an_anonymous_caller_once_an_account_exists():
    client.post("/api/auth/signup", json={"email": "chef_edi@x.fr", "password": "motdepasse1"})
    for route, files, data in (
        ("/api/edi/inspect", upload(ORDERS), {}),
        ("/api/edi/validate", upload(ORDERS), {"model_yaml": M_ORDERS}),
        ("/api/edi/pivot", upload(ORDERS), {"model_yaml": M_ORDERS, "mode": "flat", "target": "preview"}),
        ("/api/edi/models/infer", upload(ORDERS), {}),
        ("/api/edi/generate", {"file": ("f.csv", b"a;b\n1;2\n", "text/csv")}, {"model_yaml": M_ORDERS}),
        ("/api/edi/convert", upload(ORDERS), {"source_yaml": M_ORDERS, "target_yaml": M_ORDERS}),
    ):
        r = client.post(route, files=files, data=data)
        assert r.status_code == 401, f"{route}: {r.status_code} {r.text}"


def test_a_viewer_cannot_run_edi_operations_operator_capability_required():
    client.post("/api/auth/signup", json={"email": "chef_edi2@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef_edi2@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/quick-user",
               json={"email": "spectateur@x.fr", "memberships": {"default": "viewer"}}, headers=_h(chef))
    viewer = client.post("/api/auth/login",
                         json={"email": "spectateur@x.fr", "password": "motdepasse1"}).json()["token"]
    r = client.post("/api/edi/pivot", files=upload(ORDERS),
                    data={"model_yaml": M_ORDERS, "mode": "flat", "target": "preview"}, headers=_h(viewer))
    assert r.status_code == 403, r.text


def test_a_model_from_another_environment_is_invisible_not_just_refused():
    """404, not 409 (which would confirm the artefact's kind) or 403 (which
    would confirm it exists at all) — same rule as the generic artefact
    routes: an artefact one cannot see must not even prove it exists."""
    client.post("/api/auth/signup", json={"email": "chef_edi3@x.fr", "password": "motdepasse1"})
    chef = client.post("/api/auth/login",
                       json={"email": "chef_edi3@x.fr", "password": "motdepasse1"}).json()["token"]
    client.post("/api/admin/quick-user",
               json={"email": "proprio@x.fr", "memberships": {"secret_env": "editor"}}, headers=_h(chef))
    owner = client.post("/api/auth/login",
                        json={"email": "proprio@x.fr", "password": "motdepasse1"}).json()["token"]
    aid = client.post("/api/artefacts/edi_model",
                      json={"name": "modele-prive", "yaml": M_ORDERS, "environment": "secret_env"},
                      headers=_h(owner)).json()["id"]

    client.post("/api/admin/quick-user",
               json={"email": "etranger@x.fr", "memberships": {"autre_env": "operator"}}, headers=_h(chef))
    outsider = client.post("/api/auth/login",
                           json={"email": "etranger@x.fr", "password": "motdepasse1"}).json()["token"]

    r = client.post("/api/edi/pivot?env=autre_env", files=upload(ORDERS),
                    data={"model_id": aid, "mode": "flat", "target": "preview"}, headers=_h(outsider))
    assert r.status_code == 404, r.text

    y = client.get(f"/api/edi/models/{aid}/versions/1/yaml", headers=_h(outsider))
    assert y.status_code == 404, y.text

    # the owner, meanwhile, can use their own model normally
    ok = client.post("/api/edi/pivot?env=secret_env", files=upload(ORDERS),
                     data={"model_id": aid, "mode": "flat", "target": "preview"}, headers=_h(owner))
    assert ok.status_code == 200, ok.text
