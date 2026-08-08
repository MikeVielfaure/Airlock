"""
La charnière du projet : « ce que je viens de faire à la main devient un objet ».

C'est là que la philosophie se vérifie ou se dément. `test_env_functions.py`
couvrait déjà la moitié qui refuse — une cellule corrigée est de la donnée, pas
de la logique. Ces tests couvrent la moitié qui reprend, et surtout la règle qui
lie les deux : ce qui n'est ni repris ni refusable doit être **nommé**, jamais
perdu en silence.
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "QTE;PRIX;CLIENT\n3;10;Durand\n5;2;Martin\n"


def _upload() -> str:
    r = client.post("/api/files",
                    files={"file": ("v.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _process(sid, fields=None, computed=None, sql_computed=None, visible=None):
    body = {
        "visible_cols": visible if visible is not None else ["QTE", "PRIX", "CLIENT"],
        "fields": fields or {"QTE": {"name": ["QTE"], "type": "integer"},
                             "PRIX": {"name": ["PRIX"], "type": "integer"},
                             "CLIENT": {"name": ["CLIENT"], "type": "string"}},
    }
    if computed:
        body["computed"] = computed
    if sql_computed:
        body["sql_computed"] = sql_computed
    r = client.post(f"/api/files/{sid}/process", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _promouvoir(sid, **kw):
    r = client.post(f"/api/files/{sid}/to-flow",
                    json={"name": "flux-test", "save": False, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _noeud(graphe, type_):
    return next((n for n in graphe["nodes"] if n["type"] == type_), None)


# ── La logique est reprise, expression comprise ────────────────────────────

def test_l_expression_d_une_colonne_calculee_est_reprise():
    """Le cœur du correctif. L'historique n'enregistrait que les *noms*, donc la
    promotion ne pouvait écrire que `TOTAL = [TOTAL]` : une colonne calculée à
    partir d'elle-même, c'est-à-dire la formule perdue."""
    sid = _upload()
    _process(sid, computed=[{"name": "TOTAL", "expression": "[QTE] * [PRIX]"}])
    calc = _noeud(_promouvoir(sid)["graph"], "compute")
    assert calc is not None, "une colonne calculée doit produire un nœud compute"
    assert calc["config"]["columns"]["TOTAL"] == "[QTE] * [PRIX]"
    assert calc["config"]["columns"]["TOTAL"] != "[TOTAL]"


def test_le_flux_promu_recalcule_vraiment_la_colonne():
    """La preuve qui compte : le flux tourne et produit la bonne valeur. Avec
    l'ancienne configuration `{"ETIQUETTE": "[ETIQUETTE]"}`, la colonne se
    recopiait elle-même.

    L'expression est volontairement textuelle. Une expression arithmétique
    donnerait `#ERR` — non pas parce qu'elle serait mal reprise, mais parce que
    le flux promu ne rejoue pas le *nettoyage* de la session (voir le point
    signalé dans `skipped`). Mélanger les deux dans un même test rendrait
    l'échec illisible."""
    sid = _upload()
    _process(sid, computed=[{"name": "ETIQUETTE",
                             "expression": 'CONCAT([CLIENT], "-", [QTE])'}])
    body = _promouvoir(sid, save=True)
    run = client.post("/api/graphs/run", json={"graph_id": body["artefact_id"]})
    assert run.status_code == 200, run.text
    apercu = run.json()["preview"]
    assert "ETIQUETTE" in apercu["columns"], apercu["columns"]
    i = apercu["columns"].index("ETIQUETTE")
    assert sorted(l[i] for l in apercu["data"]) == ["Durand-3", "Martin-5"]


def test_les_regles_de_validation_sont_reprises():
    sid = _upload()
    _process(sid, fields={"CLIENT": {"name": ["CLIENT"], "type": "string",
                                     "regex": "^[A-Z]"}}, visible=["CLIENT"])
    check = _noeud(_promouvoir(sid)["graph"], "validate")
    assert check is not None
    assert check["config"]["rules"]["CLIENT"]["regex"] == "^[A-Z]"


# ── L'ordre suit le traitement, pas l'ordre d'insertion ───────────────────

def test_le_calcul_precede_la_validation():
    """`sess.history` reçoit `validate` avant `compute`. Parcourir la liste dans
    son ordre produisait un flux qui validait des colonnes pas encore
    calculées — l'inverse de ce que fait la session, où les règles portent aussi
    sur les colonnes dérivées."""
    sid = _upload()
    _process(sid,
             fields={"QTE": {"name": ["QTE"], "type": "integer"},
                     "PRIX": {"name": ["PRIX"], "type": "integer"},
                     "CLIENT": {"name": ["CLIENT"], "type": "string", "regex": "^[A-Z]"}},
             computed=[{"name": "TOTAL", "expression": "[QTE] * [PRIX]"}])
    graphe = _promouvoir(sid)["graph"]
    ids = [n["id"] for n in graphe["nodes"]]
    assert "calc" in ids and "check" in ids
    assert ids.index("calc") < ids.index("check"), "le calcul doit précéder la validation"

    liens = {(e["from"], e["to"]) for e in graphe["edges"]}
    assert ("src", "calc") in liens and ("calc", "check") in liens


# ── Ce qui n'est pas reprenable est nommé ─────────────────────────────────

def test_une_colonne_sql_est_signalee_et_non_perdue():
    """`duck_compute` n'a aucune brique de flux équivalente. La colonne était
    ignorée par la boucle *et* absente de `skipped` : elle disparaissait sans un
    mot, ce qui est exactement ce que cette fonction promet de ne pas faire."""
    sid = _upload()
    _process(sid, sql_computed=[{
        "name": "RANG", "mode": "replace",
        "expression": "SELECT self._row_id, CAST(self.QTE AS INTEGER) * 100 AS RANG "
                      "FROM self"}])
    dit = " ".join(_promouvoir(sid)["skipped"])
    assert "SQL" in dit and "RANG" in dit, dit


def test_un_historique_ancien_est_signale_plutot_que_trahi():
    """Une session enregistrée avant que les expressions ne soient journalisées.
    Produire `[TOTAL]` en silence donnerait un flux faux qui a l'air correct —
    le pire des deux mondes."""
    from app.db import commit, session_scope
    from app.main import store

    sid = _upload()
    _process(sid, computed=[{"name": "TOTAL", "expression": "[QTE] * [PRIX]"}])

    with session_scope() as db:                    # on rejoue la forme d'avant
        sess = store.get(db, sid)
        sess.history = [{"op": "compute", "columns": ["TOTAL"]}]
        store.save(db, sid, sess)
        commit(db)

    out = _promouvoir(sid)
    assert _noeud(out["graph"], "compute") is None, "aucun nœud faux ne doit être produit"
    dit = " ".join(out["skipped"])
    assert "expression" in dit and "TOTAL" in dit, dit


# ── Le graphe produit reste un graphe valide ──────────────────────────────

def test_deux_passages_ne_produisent_pas_de_doublon():
    """Chaque opération ne garde que sa dernière trace : deux validations ne
    doivent pas créer deux nœuds de même identifiant, ce que `FlowGraph`
    refuserait à juste titre."""
    from app.flow_graph import FlowGraph

    sid = _upload()
    _process(sid, computed=[{"name": "TOTAL", "expression": "[QTE] * [PRIX]"}])
    _process(sid, computed=[{"name": "TOTAL", "expression": "[QTE] * [PRIX] * 2"}])
    graphe = _promouvoir(sid)["graph"]
    FlowGraph(**graphe)                            # ne doit pas lever
    ids = [n["id"] for n in graphe["nodes"]]
    assert len(ids) == len(set(ids))
    assert _noeud(graphe, "compute")["config"]["columns"]["TOTAL"] == "[QTE] * [PRIX] * 2", \
        "la dernière validation gagne"
