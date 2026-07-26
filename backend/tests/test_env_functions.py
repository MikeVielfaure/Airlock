"""
Three features that share one layer — the artefact library.

  * user functions (v19): the expression language extended by its users;
  * environments (v20): the library scoped, so RH and ADV never see each other;
  * session → flow (v21): what was done by hand, made repeatable.
"""
import io

from fastapi.testclient import TestClient

from app.function_models import UserFunction, build_registry
from app.main import app
from app.services.compute_service import ComputeService

client = TestClient(app)

CSV = "SIRET;MONTANT\n12345678901234;100\n99999999999999;50\n"


def _upload(text: str = CSV):
    return client.post("/api/files",
                       files={"file": ("t.csv", io.BytesIO(text.encode()), "text/csv")},
                       data={"file_type": "CSV", "encoding": "AUTO",
                             "delimiter": ";"}).json()["session_id"]


# ══════════════════════════════════════════════════════════════════════
# v19 — user functions
# ══════════════════════════════════════════════════════════════════════
TTC = {"name": "prix_ttc", "params": ["montant", "taux"],
       "expr": "ROUND(NUM([montant]) * (1 + NUM([taux])), 2)"}


def test_a_function_is_stored_validated_and_versioned():
    r = client.post("/api/artefacts/function", json={"name": "prix_ttc", "body": TTC})
    assert r.status_code == 201, r.text
    v2 = client.post(f"/api/artefacts/function/{r.json()['id']}/versions", json={"body": TTC})
    assert v2.status_code in (200, 201)


def test_a_function_that_does_not_compile_never_reaches_the_library():
    bad = {"name": "casse", "params": ["a"], "expr": "NOPE("}
    r = client.post("/api/artefacts/function", json={"name": "casse", "body": bad})
    assert r.status_code == 422
    assert "expression" in r.json()["detail"].lower()


def test_a_function_extends_the_expression_language():
    import pandas as pd
    reg = build_registry([UserFunction(**TTC)])
    engine = ComputeService(extra_functions=reg)
    df = pd.DataFrame({"m": ["100"]})
    assert list(engine.evaluate(df, "PRIX_TTC([m], '0.2')")) == ["120.0"]


def test_parameters_shadow_columns_instead_of_merging_with_them():
    """Inside a body, [montant] is the argument — never a same-named column of
    the caller. Otherwise a function would behave differently depending on where
    it is called, which defeats reuse."""
    import pandas as pd
    reg = build_registry([UserFunction(name="ident", params=["montant"],
                                       expr="[montant]")])
    engine = ComputeService(extra_functions=reg)
    df = pd.DataFrame({"montant": ["999"]})          # a column of the same name
    assert list(engine.evaluate(df, "IDENT('7')")) == ["7"]


def test_functions_compose_and_a_cycle_is_caught_not_fatal():
    import pandas as pd
    reg = build_registry([
        UserFunction(name="doubler", params=["x"], expr="NUM([x]) * 2"),
        UserFunction(name="quadrupler", params=["x"], expr="DOUBLER(DOUBLER([x]))"),
        # mutually recursive: must degrade, never blow the stack
        UserFunction(name="boucle_a", params=["x"], expr="BOUCLE_B([x])"),
        UserFunction(name="boucle_b", params=["x"], expr="BOUCLE_A([x])"),
    ])
    engine = ComputeService(extra_functions=reg)
    df = pd.DataFrame({"n": ["3"]})
    assert list(engine.evaluate(df, "QUADRUPLER([n])")) == ["12.0"]
    assert list(engine.evaluate(df, "BOUCLE_A([n])")) == ["#ERR"]


def test_a_flow_brick_can_call_a_stored_function():
    client.post("/api/artefacts/function",
                json={"name": "majuscule_prefixee", "environment": "default",
                      "body": {"name": "majuscule_prefixee", "params": ["v"],
                               "expr": "CONCAT('X-', UPPER([v]))"}})
    y = """
name: avec-fonction
nodes:
  - {id: src, type: inline, config: {rows: [{code: "abc"}]}}
  - {id: calc, type: compute, config: {columns: {ref: "MAJUSCULE_PREFIXEE([code])"}}}
  - {id: out, type: response}
edges: [{from: src, to: calc}, {from: calc, to: out}]
"""
    r = client.post("/api/graphs/run", json={"yaml": y})
    assert r.status_code == 200, r.text
    assert "X-ABC" in str(r.json()["preview"]["data"])


# ══════════════════════════════════════════════════════════════════════
# v20 — environments
# ══════════════════════════════════════════════════════════════════════
CFG = "fields:\n  SIRET:\n    type: string\n"


def test_the_same_name_lives_in_two_environments_without_colliding():
    a = client.post("/api/artefacts/config",
                    json={"name": "clients", "yaml": CFG, "environment": "rh"})
    b = client.post("/api/artefacts/config",
                    json={"name": "clients", "yaml": CFG, "environment": "adv"})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]

    again = client.post("/api/artefacts/config",
                        json={"name": "clients", "yaml": CFG, "environment": "rh"})
    assert again.status_code == 409          # still unique *within* rh


def test_listing_is_scoped_and_never_leaks_another_environment():
    client.post("/api/artefacts/config",
                json={"name": "paie", "yaml": CFG, "environment": "rh"})
    client.post("/api/artefacts/config",
                json={"name": "commandes", "yaml": CFG, "environment": "adv"})

    rh = [a["name"] for a in client.get("/api/artefacts/config?env=rh").json()]
    adv = [a["name"] for a in client.get("/api/artefacts/config?env=adv").json()]
    assert "paie" in rh and "commandes" not in rh
    assert "commandes" in adv and "paie" not in adv


def test_forgetting_the_scope_shows_the_default_not_everything():
    """A query that omits the environment must show *less*, never leak."""
    client.post("/api/artefacts/config",
                json={"name": "secret-rh", "yaml": CFG, "environment": "rh"})
    names = [a["name"] for a in client.get("/api/artefacts/config").json()]
    assert "secret-rh" not in names


def test_star_deliberately_shows_every_environment():
    client.post("/api/artefacts/config",
                json={"name": "vu-partout", "yaml": CFG, "environment": "rh"})
    names = [a["name"] for a in client.get("/api/artefacts/config?env=*").json()]
    assert "vu-partout" in names


def test_environments_are_advertised_for_the_selector():
    client.post("/api/artefacts/config",
                json={"name": "x1", "yaml": CFG, "environment": "logistique"})
    body = client.get("/api/environments").json()
    assert "logistique" in body["environments"]
    assert body["default"] == "default"


def test_a_function_of_one_environment_is_invisible_to_another():
    client.post("/api/artefacts/function",
                json={"name": "seulement_rh", "environment": "rh",
                      "body": {"name": "seulement_rh", "params": ["x"], "expr": "[x]"}})
    y = """
name: hors-scope
nodes:
  - {id: src, type: inline, config: {rows: [{a: "1"}]}}
  - {id: calc, type: compute, config: {columns: {b: "SEULEMENT_RH([a])"}}}
  - {id: out, type: response}
edges: [{from: src, to: calc}, {from: calc, to: out}]
"""
    ko = client.post("/api/graphs/run", json={"yaml": y, "environment": "adv"})
    assert ko.status_code == 422                     # not visible from adv
    ok = client.post("/api/graphs/run", json={"yaml": y, "environment": "rh"})
    assert ok.status_code == 200, ok.text


# ══════════════════════════════════════════════════════════════════════
# v21 — a hand-made session becomes a flow
# ══════════════════════════════════════════════════════════════════════
def test_a_session_becomes_a_runnable_flow():
    sid = _upload()
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET", "MONTANT"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string", "regex": r"^\d{14}$"},
                   "MONTANT": {"name": ["MONTANT"], "type": "integer"}}})

    r = client.post(f"/api/files/{sid}/to-flow",
                    json={"name": "flux-depuis-session", "save": True})
    assert r.status_code == 200, r.text
    body = r.json()
    types = [n["type"] for n in body["graph"]["nodes"]]
    assert types[0] == "session" and types[-1] == "response"
    assert "validate" in types                       # the rules were carried over
    assert body["artefact_id"]

    run = client.post("/api/graphs/run", json={"graph_id": body["artefact_id"]})
    assert run.status_code == 200, run.text
    assert run.json()["preview"]["total_rows"] == 2


def test_hand_edits_are_reported_as_skipped_not_baked_into_the_flow():
    """A corrected cell is data about one file, not logic. Turning it into a
    flow step would silently corrupt the next file the flow touches."""
    sid = _upload()
    idx = client.get(f"/api/files/{sid}/preview").json()["index"][0]
    client.post(f"/api/files/{sid}/cells",
                json={"edits": [{"index": idx, "column": "SIRET", "value": "11111111111111"}]})
    client.post(f"/api/files/{sid}/rows/delete", json={"indices": [idx]})
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"}}})

    body = client.post(f"/api/files/{sid}/to-flow",
                       json={"name": "flux-avec-retouches", "save": False}).json()
    assert body["artefact_id"] is None               # save: false
    joined = " ".join(body["skipped"])
    assert "cellule" in joined and "ligne" in joined
    assert all(n["type"] != "edit" for n in body["graph"]["nodes"])


def test_a_flow_born_from_a_session_can_be_pointed_at_a_table_instead():
    sid = _upload()
    client.post(f"/api/files/{sid}/process", json={
        "visible_cols": ["SIRET"],
        "fields": {"SIRET": {"name": ["SIRET"], "type": "string"}}})
    body = client.post(f"/api/files/{sid}/to-flow",
                       json={"name": "flux-durable", "save": False,
                             "source": "dataset", "dataset_name": "clients_prod"}).json()
    src = body["graph"]["nodes"][0]
    assert src["type"] == "dataset" and src["config"]["name"] == "clients_prod"
