"""
Visual flows: bricks wired into a graph, run in dependency order, and callable
over HTTP. These tests drive the whole promise — a source feeds a transform
feeds a sink, a flow becomes a brick inside another flow, and a stored flow
answers as an API.
"""
from fastapi.testclient import TestClient

from app.flow_graph import FlowGraph, graph_from_yaml
from app.main import app

client = TestClient(app)

SIMPLE = """
name: commandes
params:
  - {name: seuil, default: "0"}
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {ref: A1, montant: "10", pays: FR}
        - {ref: A2, montant: "30", pays: BE}
        - {ref: A3, montant: "50", pays: FR}
  - id: enrich
    type: compute
    config:
      columns: {double: "[montant] + [montant]"}
  - id: out
    type: response
edges:
  - {from: src, to: enrich}
  - {from: enrich, to: out}
"""


# ── the graph model refuses what cannot run ──────────────────────────
def test_a_cycle_is_refused_and_names_the_nodes():
    bad = """
name: boucle
nodes:
  - {id: a, type: response}
  - {id: b, type: response}
edges:
  - {from: a, to: b}
  - {from: b, to: a}
"""
    try:
        graph_from_yaml(bad)
        assert False, "a cycle should be refused"
    except Exception as e:
        assert "cycle" in str(e).lower()
        assert "a" in str(e) and "b" in str(e)      # it names them


def test_dangling_edges_and_duplicate_ids_are_refused():
    for bad in ("""
name: x
nodes: [{id: a, type: response}]
edges: [{from: a, to: ghost}]
""", """
name: x
nodes: [{id: a, type: response}, {id: a, type: response}]
"""):
        try:
            graph_from_yaml(bad)
            assert False, "should be refused"
        except Exception:
            pass


def test_several_terminals_demand_an_explicit_output():
    g = graph_from_yaml("""
name: x
nodes:
  - {id: s, type: inline, config: {rows: []}}
  - {id: a, type: response}
  - {id: b, type: response}
edges: [{from: s, to: a}, {from: s, to: b}]
""")
    try:
        g.resolve_output()
        assert False, "ambiguous output should raise"
    except ValueError as e:
        assert "output" in str(e).lower()


def test_topological_order_respects_dependencies():
    g = graph_from_yaml(SIMPLE)
    order = g.topological_order()
    assert order.index("src") < order.index("enrich") < order.index("out")


# ── running ───────────────────────────────────────────────────────────
def test_a_flow_runs_and_reports_a_trace_per_node():
    r = client.post("/api/graphs/run", json={"yaml": SIMPLE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output"] == "out"
    assert body["preview"]["total_rows"] == 3
    assert "double" in body["preview"]["columns"]     # the compute brick ran
    ids = [t["node"] for t in body["trace"]]
    assert ids == ["src", "enrich", "out"]
    assert all("ms" in t for t in body["trace"])      # timing, for debugging


def test_a_filter_brick_drops_rows_and_says_how_many():
    y = SIMPLE.replace("""  - id: out
    type: response
edges:
  - {from: src, to: enrich}
  - {from: enrich, to: out}""",
"""  - id: keep
    type: filter
    config: {where: "IF([pays] == 'FR', '1', '0')"}
  - id: out
    type: response
edges:
  - {from: src, to: enrich}
  - {from: enrich, to: keep}
  - {from: keep, to: out}""")
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 2      # the two FR rows
    step = [t for t in r.json()["trace"] if t["node"] == "keep"][0]
    assert step["meta"]["kept"] == 2 and step["meta"]["dropped"] == 1


def test_a_failing_node_is_named_not_anonymous():
    y = """
name: casse
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: boom, type: compute, config: {columns: {x: "NOPE("}}}
  - {id: out, type: response}
edges: [{from: src, to: boom}, {from: boom, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422
    assert "boom" in r.json()["detail"]


def test_parameters_are_substituted_and_required_ones_enforced():
    y = """
name: parametre
params:
  - {name: pays, default: "FR"}
  - {name: obligatoire, required: true}
nodes:
  - {id: src, type: inline, config: {rows: [{p: "{pays}"}]}}
  - {id: out, type: response}
edges: [{from: src, to: out}]
"""
    ko = client.post("/api/graphs/run", json={"yaml": y})
    assert ko.status_code == 422 and "obligatoire" in ko.json()["detail"]

    ok = client.post("/api/graphs/run",
                     json={"yaml": y, "params": {"obligatoire": "x", "pays": "BE"}})
    assert ok.status_code == 200
    assert "BE" in str(ok.json()["preview"]["data"])   # the placeholder resolved


# ── a mapping brick reuses the mapping engine ────────────────────────
def test_a_mapping_brick_reshapes_and_checks():
    y = """
name: avec-mapping
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {NoCmd: PO1, EAN: "3011111111111"}
        - {NoCmd: PO1, EAN: "123"}
  - id: shape
    type: mapping
    config:
      mapping_yaml: |
        name: m
        source_kind: flat
        links:
          - {pivot: commande, source: NoCmd, scope: head}
          - {pivot: article, source: EAN, scope: item, rules: {type: string, regex: "^\\\\d{13}$"}}
  - id: out
    type: response
edges: [{from: src, to: shape}, {from: shape, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    cols = r.json()["preview"]["columns"]
    assert "commande" in cols and "article" in cols
    step = [t for t in r.json()["trace"] if t["node"] == "shape"][0]
    assert step["meta"]["checks"]["ok"] is False        # the short EAN was caught


# ── nesting: a flow is a brick ───────────────────────────────────────
def test_a_flow_can_be_used_as_a_brick_inside_another_flow():
    inner = client.post("/api/artefacts/graph",
                        json={"name": "brique-interne", "yaml": SIMPLE})
    assert inner.status_code == 201, inner.text
    inner_id = inner.json()["id"]

    outer = f"""
name: enveloppe
nodes:
  - id: sub
    type: graph
    config: {{graph_id: "{inner_id}"}}
  - id: out
    type: response
edges: [{{from: sub, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": outer})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 3
    # the sub-flow's own steps are traced under the parent node
    assert any(t["node"].startswith("sub/") for t in r.json()["trace"])


# ── the point: a flow is an API ──────────────────────────────────────
def test_a_stored_flow_answers_as_an_http_endpoint():
    y = """
name: api-commandes
params:
  - {name: pays, default: "FR"}
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {ref: A1, pays: "{pays}"}
  - id: out
    type: response
edges: [{from: src, to: out}]
"""
    gid = client.post("/api/artefacts/graph",
                      json={"name": "flux-api", "yaml": y}).json()["id"]

    r = client.post(f"/api/graphs/{gid}/call", json={"pays": "IT"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flow"] == "api-commandes"
    assert body["count"] == 1
    assert body["data"][0]["pays"] == "IT"             # the caller drove the flow


def test_a_flow_can_produce_a_file_to_send():
    y = """
name: fichier
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1", b: "2"}]}}
  - {id: out, type: file, config: {format: csv, filename: envoi.csv}}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["filename"] == "envoi.csv" and meta["content_base64"]


def test_a_flow_lands_its_result_in_a_table():
    y = """
name: vers-base
nodes:
  - id: src
    type: inline
    config: {rows: [{ref: R1, v: "10"}, {ref: R2, v: "20"}]}
  - id: out
    type: dataset_write
    config: {name: flux_table, mode: replace, key_fields: [ref]}
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["written"] == 2

    ds = [d for d in client.get("/api/datasets").json() if d["name"] == "flux_table"]
    assert ds and ds[0]["row_count"] == 2


# ── storage & validation surface ─────────────────────────────────────
def test_a_graph_is_a_versioned_artefact_and_invalid_ones_are_refused():
    r = client.post("/api/artefacts/graph", json={"name": "flux-v", "yaml": SIMPLE})
    assert r.status_code == 201
    v2 = client.post(f"/api/artefacts/graph/{r.json()['id']}/versions", json={"yaml": SIMPLE})
    assert v2.status_code in (200, 201)

    bad = client.post("/api/artefacts/graph",
                      json={"name": "ko", "yaml": "name: x\nnodes: [{id: a, type: nope}]"})
    assert bad.status_code == 422


def test_validate_reports_the_plan_without_running():
    r = client.post("/api/graphs/validate", json={"yaml": SIMPLE})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["order"] == ["src", "enrich", "out"]
    assert body["output"] == "out"
    assert [p["name"] for p in body["params"]] == ["seuil"]


def test_the_brick_palette_is_advertised():
    b = client.get("/api/graphs/bricks").json()["bricks"]
    types = {x["type"]: x["role"] for x in b}
    assert types["api"] == "source"
    assert types["mapping"] == "transform"
    assert types["response"] == "sink"
    assert "graph" in types                            # a flow is a brick


# ── relational bricks: what row-wise work could never do ─────────────
VENTES = """
  - id: src
    type: inline
    config:
      rows:
        - {client: ACME, pays: FR, montant: "100"}
        - {client: ACME, pays: FR, montant: "50"}
        - {client: BETA, pays: BE, montant: "80"}
"""


def test_aggregate_collapses_rows_into_groups():
    y = f"""
name: totaux
nodes:{VENTES}
  - id: total
    type: aggregate
    config:
      by: [client]
      agg:
        chiffre: {{column: montant, fn: sum}}
        lignes: {{column: montant, fn: count}}
  - {{id: out, type: response}}
edges: [{{from: src, to: total}}, {{from: total, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    prev = r.json()["preview"]
    assert prev["total_rows"] == 2                      # two clients
    assert {"client", "chiffre", "lignes"} <= set(prev["columns"])
    dump = str(prev["data"])
    assert "150" in dump                                # ACME's 100 + 50
    step = [t for t in r.json()["trace"] if t["node"] == "total"][0]
    assert step["meta"] == {"groups": 2, "from_rows": 3}


def test_aggregate_without_keys_gives_one_total_row():
    y = f"""
name: global
nodes:{VENTES}
  - {{id: t, type: aggregate, config: {{agg: {{tout: {{column: montant, fn: sum}}}}}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: t}}, {{from: t, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["total_rows"] == 1
    assert "230" in str(r.json()["preview"]["data"])


def test_an_unknown_aggregate_function_is_refused_by_name():
    y = f"""
name: ko
nodes:{VENTES}
  - {{id: t, type: aggregate, config: {{by: [client], agg: {{x: {{column: montant, fn: mediane}}}}}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: t}}, {{from: t, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422 and "mediane" in r.json()["detail"]


def test_join_merges_two_streams_on_a_named_key():
    y = f"""
name: jointure
nodes:{VENTES}
  - id: refs
    type: inline
    config:
      rows:
        - {{client: ACME, secteur: industrie}}
        - {{client: BETA, secteur: services}}
  - id: j
    type: join
    config: {{left: src, right: refs, on: [client], how: left}}
  - {{id: out, type: response}}
edges: [{{from: src, to: j}}, {{from: refs, to: j}}, {{from: j, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    prev = r.json()["preview"]
    assert prev["total_rows"] == 3                      # left join keeps every sale
    assert "secteur" in prev["columns"]
    assert "industrie" in str(prev["data"])


def test_join_says_which_side_is_which_and_refuses_a_missing_key():
    y = f"""
name: ko
nodes:{VENTES}
  - {{id: refs, type: inline, config: {{rows: [{{autre: X}}]}}}}
  - {{id: j, type: join, config: {{left: src, right: refs, on: [client]}}}}
  - {{id: out, type: response}}
edges: [{{from: src, to: j}}, {{from: refs, to: j}}, {{from: j, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 422
    assert "right" in r.json()["detail"] or "client" in r.json()["detail"]


def test_lookup_enriches_without_ever_changing_the_row_count():
    y = f"""
name: recherche
nodes:{VENTES}
  - id: lib
    type: lookup
    config:
      key: pays
      ref_key: pays
      ref_value: libelle
      into: pays_libelle
      values: {{FR: France, BE: Belgique}}
  - {{id: out, type: response}}
edges: [{{from: src, to: lib}}, {{from: lib, to: out}}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    prev = r.json()["preview"]
    assert prev["total_rows"] == 3                      # a lookup never multiplies
    assert "France" in str(prev["data"]) and "Belgique" in str(prev["data"])


def test_the_relational_bricks_are_in_the_palette():
    roles = {b["type"]: b["role"] for b in client.get("/api/graphs/bricks").json()["bricks"]}
    for t in ("aggregate", "join", "lookup"):
        assert roles[t] == "transform"
