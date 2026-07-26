"""
The `config` brick: replay a stored configuration over records, exactly as
pressing *Valider* does in the interface. Chaining is then just two of them in a
row — an adaptation that reshapes a partner's file, then the contract that
judges the result.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FX_MASTER_KEY", "cle-test-brique")

from app.main import app                     # noqa: E402

client = TestClient(app)


def _u():
    return os.urandom(3).hex()


# The contract: internal names, a strict matricule, a date in AAAAMMJJ.
CONTRAT = """type: CSV
delimiter: ";"
Fields:
  - name: [MATRICULE]
    type: string
    regex: "^M\\\\d{4}$"
    nullable: false
  - name: [SERVICE]
    type: string
"""

# The adaptation: the partner's own headers, trimmed and upper-cased, renamed
# onto the contract's names.
ADAPTATION = """type: CSV
delimiter: ";"
Fields:
  - name: [Matr.]
    mapping: MATRICULE
    rename_output: true
    type: string
    trim: true
    normalize_case: upper
  - name: [Departement]
    mapping: SERVICE
    rename_output: true
    type: string
"""


def _cfg(yaml_text, name=None):
    return client.post("/api/artefacts/config",
                       json={"name": name or f"cfg-{_u()}", "yaml": yaml_text}).json()


def _flow(nodes_yaml):
    return client.post("/api/graphs/run", json={"yaml": nodes_yaml})


# ── one configuration, replayed ──────────────────────────────────────
def test_a_stored_configuration_cleans_and_checks_like_the_interface():
    cfg = _cfg(CONTRAT)
    y = f"""
name: controle
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {{MATRICULE: "M0001", SERVICE: "RH"}}
        - {{MATRICULE: "oups", SERVICE: "ADV"}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    step = [s for s in r.json()["trace"] if s["node"] == "ctl"][0]
    assert step["meta"]["errors"] == 1
    assert step["meta"]["problems"][0]["column"] == "MATRICULE"
    # keeping and reporting is the default: dropping silently is how a flow
    # quietly loses a tenth of its input
    assert step["meta"]["rows"] == 2 and step["meta"]["on_error"] == "keep"


def test_a_configuration_can_be_named_instead_of_referenced_by_id():
    """A hand-written flow reads better with a name than a hex string."""
    name = f"contrat-{_u()}"
    _cfg(CONTRAT, name)
    y = f"""
name: par-nom
nodes:
  - {{id: src, type: inline, config: {{rows: [{{MATRICULE: "M0001", SERVICE: "RH"}}]}}}}
  - {{id: ctl, type: config, config: {{name: "{name}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
"""
    assert _flow(y).status_code == 200


def test_the_cleaned_values_are_what_flows_on():
    cfg = _cfg(ADAPTATION)
    y = f"""
name: nettoyage
nodes:
  - {{id: src, type: inline, config: {{rows: [{{"Matr.": "  m0001  ", Departement: "Paie"}}]}}}}
  - {{id: adapt, type: config, config: {{config_id: "{cfg['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: adapt}}, {{from: adapt, to: out}}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    data = str(r.json()["preview"]["data"])
    assert "M0001" in data                      # trimmed and upper-cased
    assert "MATRICULE" in r.json()["preview"]["columns"]   # renamed to the contract


# ── the point: chaining ──────────────────────────────────────────────
def test_an_adaptation_then_the_contract_passes_a_file_that_would_have_failed():
    """The partner's file cannot satisfy the contract as it arrives. Adapted
    first, it does — and the contract itself was never touched."""
    contrat = _cfg(CONTRAT, f"contrat-{_u()}")
    adapt = _cfg(ADAPTATION, f"adapt-{_u()}")

    raw = '{"Matr.": " m0002 ", Departement: "Compta"}'
    # straight into the contract: nothing matches
    direct = _flow(f"""
name: direct
nodes:
  - {{id: src, type: inline, config: {{rows: [{raw}]}}}}
  - {{id: ctl, type: config, config: {{config_id: "{contrat['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
""")
    # A contract that finds none of its columns must say so, loudly. Passing
    # silently would be far worse than failing: nothing was actually verified.
    assert direct.status_code == 422
    assert "MATRICULE" in direct.json()["detail"]

    # adapted first, then judged: clean
    chained = _flow(f"""
name: enchaine
nodes:
  - {{id: src, type: inline, config: {{rows: [{raw}]}}}}
  - {{id: adapt, type: config, config: {{config_id: "{adapt['id']}"}}}}
  - {{id: ctl, type: config, config: {{config_id: "{contrat['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: adapt}}, {{from: adapt, to: ctl}}, {{from: ctl, to: out}}]
""")
    assert chained.status_code == 200, chained.text
    final = [s for s in chained.json()["trace"] if s["node"] == "ctl"][0]
    assert final["meta"]["errors"] == 0
    assert "M0002" in str(chained.json()["preview"]["data"])


def test_three_configurations_chain_as_readily_as_two():
    """Nothing special about two: the brick composes."""
    a = _cfg(ADAPTATION)
    b = _cfg(CONTRAT)
    c = _cfg(CONTRAT)
    y = f"""
name: triple
nodes:
  - {{id: src, type: inline, config: {{rows: [{{"Matr.": "m0003", Departement: "RH"}}]}}}}
  - {{id: n1, type: config, config: {{config_id: "{a['id']}"}}}}
  - {{id: n2, type: config, config: {{config_id: "{b['id']}"}}}}
  - {{id: n3, type: config, config: {{config_id: "{c['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: n1}}, {{from: n1, to: n2}}, {{from: n2, to: n3}},
        {{from: n3, to: out}}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    assert [s for s in r.json()["trace"] if s["node"] == "n3"][0]["meta"]["errors"] == 0


# ── the fate of failing rows is a decision, never a surprise ─────────
def test_failing_rows_can_be_dropped_or_can_block():
    cfg = _cfg(CONTRAT)
    rows = '[{MATRICULE: "M0001", SERVICE: "RH"}, {MATRICULE: "non", SERVICE: "ADV"}]'

    drop = _flow(f"""
name: drop
nodes:
  - {{id: src, type: inline, config: {{rows: {rows}}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", on_error: drop}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
""")
    meta = [s for s in drop.json()["trace"] if s["node"] == "ctl"][0]["meta"]
    assert meta["dropped"] == 1 and meta["rows"] == 1

    block = _flow(f"""
name: block
nodes:
  - {{id: src, type: inline, config: {{rows: {rows}}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", on_error: block}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
""")
    assert block.status_code == 422 and "ctl" in block.json()["detail"]


def test_an_unknown_configuration_names_the_node_that_asked_for_it():
    y = """
name: ko
nodes:
  - {id: src, type: inline, config: {rows: [{A: "1"}]}}
  - {id: ctl, type: config, config: {name: "jamais-vue"}}
  - {id: out, type: response}
edges: [{from: src, to: ctl}, {from: ctl, to: out}]
"""
    r = _flow(y)
    assert r.status_code == 422 and "ctl" in r.json()["detail"]


def test_a_correspondence_table_can_be_supplied_to_the_configuration():
    """The very case this brick exists for: a partner's labels turned into
    internal codes."""
    tco = client.post("/api/artefacts/tco", json={
        "name": f"corr-{_u()}",
        "csv": "SOURCE_VALUE;TARGET_LABEL\nPaie;RH\n"}).json()
    cfg = _cfg("""type: CSV
delimiter: ";"
Fields:
  - name: [SERVICE]
    type: string
    tco_replace: true
""")
    y = f"""
name: avec-tco
nodes:
  - {{id: src, type: inline, config: {{rows: [{{SERVICE: "Paie"}}]}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", tco_id: "{tco['id']}"}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    assert "RH" in str(r.json()["preview"]["data"])


def test_the_brick_is_advertised_in_the_palette():
    roles = {b["type"]: b["role"] for b in client.get("/api/graphs/bricks").json()["bricks"]}
    assert roles["config"] == "transform"


def test_optional_columns_can_be_allowed_explicitly():
    """A configuration may legitimately declare a column that is not always
    there — but saying so has to be deliberate."""
    cfg = _cfg(CONTRAT)
    y = f"""
name: partiel
nodes:
  - {{id: src, type: inline, config: {{rows: [{{MATRICULE: "M0001"}}]}}}}
  - {{id: ctl, type: config, config: {{config_id: "{cfg['id']}", require_columns: false}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: ctl}}, {{from: ctl, to: out}}]
"""
    r = _flow(y)
    assert r.status_code == 200, r.text
    meta = [s for s in r.json()["trace"] if s["node"] == "ctl"][0]["meta"]
    assert meta["missing_columns"] == ["SERVICE"]     # reported even when tolerated
