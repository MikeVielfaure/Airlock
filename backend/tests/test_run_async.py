"""
Lancer un flux sans tenir la connexion.

`POST /run` attend la fin. C'est juste dans l'éditeur, faux pour un traitement
de volume déclenché par une machine : le run dépend alors de la survie d'une
connexion HTTP, ne dit rien de son avancement, et ne s'annule pas.

Ce que ces tests fixent surtout, c'est la **ligne de partage** : ce qui peut
échouer avant l'exécution doit encore échouer tout de suite. Un appelant qui
s'est trompé de graphe ou de paramètre doit l'apprendre dans sa réponse, pas
dans un journal trois minutes plus tard.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SIMPLE = """
name: flux-async
nodes:
  - id: src
    type: inline
    config:
      rows:
        - {ref: A1, montant: "10"}
        - {ref: A2, montant: "30"}
        - {ref: A3, montant: "50"}
  - id: out
    type: response
edges: [{from: src, to: out}]
"""


def _stocke(nom: str, y: str) -> str:
    r = client.post("/api/artefacts/graph", json={"name": nom, "yaml": y})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Le contrat de la réponse ───────────────────────────────────────────────

def test_la_reponse_porte_un_identifiant_et_de_quoi_suivre():
    r = client.post("/api/graphs/run-async", json={"yaml": SIMPLE})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "running"
    assert b["run_id"]
    assert b["poll"] == f"/api/ops/runs/{b['run_id']}"


def test_le_run_est_visible_dans_le_journal():
    """Le suivi ne demande rien de neuf : c'est la table qui alimente déjà
    l'écran Exploitation, donc un run lancé par l'API y apparaît comme les
    autres, sans code de suivi dédié."""
    rid = client.post("/api/graphs/run-async", json={"yaml": SIMPLE}).json()["run_id"]
    d = client.get(f"/api/ops/runs/{rid}")
    assert d.status_code == 200, d.text
    assert d.json()["id"] == rid
    assert d.json()["graph_name"] == "flux-async"


def test_le_run_se_termine_et_le_journal_le_dit():
    rid = client.post("/api/graphs/run-async", json={"yaml": SIMPLE}).json()["run_id"]
    etat = client.get(f"/api/ops/runs/{rid}").json()
    assert etat["status"] == "success", etat
    assert etat["rows_out"] == 3


def test_un_flux_stocke_se_lance_aussi():
    gid = _stocke("flux-async-stocke", SIMPLE)
    r = client.post("/api/graphs/run-async", json={"graph_id": gid})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/ops/runs/{r.json()['run_id']}").json()["status"] == "success"


# ── La ligne de partage : ce qui échoue encore tout de suite ──────────────

def test_un_graphe_introuvable_echoue_dans_la_reponse():
    r = client.post("/api/graphs/run-async", json={"graph_id": "nexiste-pas"})
    assert r.status_code in (404, 422), r.text


def test_un_yaml_invalide_echoue_dans_la_reponse():
    r = client.post("/api/graphs/run-async", json={"yaml": "nodes: [{id: a}]\nedges: []"})
    assert r.status_code == 422, r.text


def test_aucun_run_n_est_ouvert_quand_le_lancement_est_refuse():
    """Un refus ne doit pas laisser de ligne « running » orpheline : sinon
    l'écran Exploitation se remplit de runs qui n'ont jamais commencé."""
    avant = len(client.get("/api/ops/runs?status=running").json()["runs"])
    client.post("/api/graphs/run-async", json={"graph_id": "nexiste-pas"})
    client.post("/api/graphs/run-async", json={"yaml": "nodes: [{id: a}]\nedges: []"})
    apres = len(client.get("/api/ops/runs?status=running").json()["runs"])
    assert apres == avant


# ── L'échec métier est journalisé, pas perdu ─────────────────────────────

def test_un_flux_qui_casse_finit_en_erreur_dans_le_journal():
    """En tâche de fond, personne n'attend l'exception : le seul endroit où
    l'échec peut être dit, c'est le journal."""
    # Une brique `api` pointée sur `file://` : `net_guard` la refuse, ce qui
    # remonte en `FlowError`. Un échec franc, et pas un `#ERR` par cellule —
    # une expression sur une colonne absente n'aurait pas levé, elle aurait
    # produit un résultat marqué, ce qui ne teste pas ce qu'on veut ici.
    casse = """
name: flux-casse
nodes:
  - id: src
    type: api
    config:
      url: "file:///etc/passwd"
      method: GET
  - id: out
    type: response
edges: [{from: src, to: out}]
"""
    r = client.post("/api/graphs/run-async", json={"yaml": casse})
    assert r.status_code == 200, "le lancement réussit ; c'est l'exécution qui échoue"
    etat = client.get(f"/api/ops/runs/{r.json()['run_id']}").json()
    assert etat["status"] == "error", etat
    assert etat["error"]


# ── Le chemin synchrone n'a pas bougé ────────────────────────────────────

def test_le_run_synchrone_rend_toujours_son_resultat():
    """`run_and_record` a été scindé en `open_run` + `execute_run` ; le chemin
    de l'éditeur doit se comporter exactement comme avant."""
    r = client.post("/api/graphs/run", json={"yaml": SIMPLE})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] and b["run_id"]
    assert b["preview"]["total_rows"] == 3
