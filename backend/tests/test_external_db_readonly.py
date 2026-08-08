"""
Ce qu'une requête vers une base externe a le droit de faire.

Le module annonçait « read-only » sans l'appliquer. Ces tests fixent les deux
moitiés du contrat : ce qui doit être refusé, et — au moins aussi important —
ce qui ne doit **pas** l'être. Un garde-fou trop zélé sur du SQL légitime se
fait désactiver au premier ticket, et on se retrouve sans garde-fou du tout.
"""
import os

import pytest

from app.services.external_db import (
    QueryNotReadOnly, QueryTooLarge, assert_read_only, run_query,
)


# ── Ce qui est refusé ──────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "DROP TABLE clients",
    "DELETE FROM clients WHERE 1=1",
    "UPDATE clients SET actif = false",
    "INSERT INTO clients (nom) VALUES ('x')",
    "TRUNCATE clients",
    "ALTER TABLE clients ADD COLUMN x int",
    "GRANT ALL ON clients TO public",
    "CREATE TABLE t (a int)",
])
def test_les_ecritures_evidentes_sont_refusees(query):
    with pytest.raises(QueryNotReadOnly):
        assert_read_only(query)


def test_deux_instructions_refusees():
    """Le contournement le plus court : le premier mot est irréprochable."""
    with pytest.raises(QueryNotReadOnly, match="une seule instruction|;"):
        assert_read_only("SELECT 1; DROP TABLE clients")


def test_ecriture_dans_un_with_refusee():
    """PostgreSQL accepte les CTE qui écrivent. Vérifier seulement le premier
    mot laisserait passer une suppression complète."""
    with pytest.raises(QueryNotReadOnly, match="DELETE"):
        assert_read_only(
            "WITH partis AS (DELETE FROM clients RETURNING *) SELECT * FROM partis")


def test_ecriture_cachee_derriere_un_commentaire():
    with pytest.raises(QueryNotReadOnly):
        assert_read_only("SELECT 1 /* rien à voir */ ; DROP TABLE clients")


def test_ecriture_apres_un_identifiant_cite():
    """Peu importe lequel des trois verrous répond — ce qui compte est que
    rien ne passe."""
    with pytest.raises(QueryNotReadOnly):
        assert_read_only('SELECT * FROM "t" ; DROP TABLE clients')


def test_requete_vide_refusee():
    with pytest.raises(QueryNotReadOnly, match="vide"):
        assert_read_only("   \n  ")


# ── Ce qui doit continuer de passer ────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "SELECT * FROM clients",
    "select nom, prenom from clients where actif = :actif",
    "SELECT * FROM clients ORDER BY nom LIMIT 100",
    "WITH recents AS (SELECT * FROM commandes WHERE d > :d) SELECT * FROM recents",
    "SELECT * FROM clients;",                       # le « ; » final est toléré
    "-- export du jour\nSELECT * FROM clients",
    "SELECT a.*, b.libelle FROM commandes a JOIN refs b ON a.code = b.code",
    "SELECT COUNT(*) AS n FROM clients GROUP BY pays HAVING COUNT(*) > 1",
])
def test_les_lectures_passent(query):
    assert_read_only(query)


@pytest.mark.parametrize("query", [
    "SELECT deleted_at, created_at, update_time FROM clients",   # mots entiers seulement
    "SELECT * FROM dataset_lignes OFFSET 10",                    # « dataset », « offset »
    "SELECT * FROM copy_log WHERE analyze_id = :i",
    "SELECT * FROM clients WHERE statut = 'set' AND note = 'a;b'",
    "SELECT deleted FROM clients",
    'SELECT * FROM "drop" WHERE nom = \'DELETE FROM clients\'',   # cités = données, pas verbes
])
def test_pas_de_faux_positif_sur_du_sql_ordinaire(query):
    """La liste des verbes est cherchée en mot entier. `update_time`,
    `dataset`, `offset` et un « ; » à l'intérieur d'une chaîne ne sont pas
    des écritures — et si ce test cassait, le garde-fou deviendrait le
    problème à contourner plutôt que la protection."""
    assert_read_only(query)


# ── Bout à bout, sur une vraie base ────────────────────────────────────────

def _sqlite(tmp_path):
    import sqlalchemy
    url = f"sqlite:///{tmp_path}/externe.db"
    e = sqlalchemy.create_engine(url)
    with e.connect() as c:
        c.execute(sqlalchemy.text("CREATE TABLE clients (id int, nom text)"))
        c.execute(sqlalchemy.text("INSERT INTO clients VALUES (1, 'Durand'), (2, 'Martin')"))
        c.commit()
    e.dispose()
    return url


def test_lecture_reelle(tmp_path):
    url = _sqlite(tmp_path)
    df = run_query(url, "SELECT nom FROM clients ORDER BY id", {}, 10)
    assert list(df["nom"]) == ["Durand", "Martin"]


def test_les_parametres_restent_lies(tmp_path):
    """La protection ajoutée ne doit pas avoir cassé la paramétrisation, qui
    était déjà la partie correcte de ce module."""
    url = _sqlite(tmp_path)
    df = run_query(url, "SELECT nom FROM clients WHERE id = :i", {"i": 2}, 10)
    assert list(df["nom"]) == ["Martin"]


def test_une_ecriture_ne_touche_jamais_la_base(tmp_path):
    """La table doit être intacte après la tentative — c'est la seule preuve
    qui compte, et elle vaut mieux que « une exception a été levée »."""
    url = _sqlite(tmp_path)
    with pytest.raises(QueryNotReadOnly):
        run_query(url, "DELETE FROM clients", {}, 10)
    assert len(run_query(url, "SELECT * FROM clients", {}, 10)) == 2


def test_trop_de_lignes(tmp_path):
    url = _sqlite(tmp_path)
    with pytest.raises(QueryTooLarge):
        run_query(url, "SELECT * FROM clients", {}, 1)


# ── Le troisième verrou, celui qui ne dépend pas de ce module ─────────────

_PG = os.getenv("FX_DB_URL", "").startswith("postgresql")


@pytest.mark.skipif(not _PG, reason="demande PostgreSQL (FX_DB_URL), comme la matrice de CI")
def test_postgres_refuse_l_ecriture_meme_si_l_analyse_est_contournee(monkeypatch):
    """La preuve que les trois verrous ne sont pas trois fois le même.

    Les verrous 1 et 2 sont une analyse de chaîne écrite à la main : ils
    peuvent avoir tort. Le troisième est tenu par le serveur de base. On
    neutralise donc volontairement les deux premiers et on vérifie que
    PostgreSQL refuse quand même — si ce test tombait, cela voudrait dire que
    `SET TRANSACTION READ ONLY` n'a jamais été émis et que toute la sûreté
    reposait sur une expression régulière.
    """
    import sqlalchemy

    from app.services import external_db

    url = os.environ["FX_DB_URL"]
    e = sqlalchemy.create_engine(url)
    with e.connect() as c:
        c.execute(sqlalchemy.text("DROP TABLE IF EXISTS _ro_probe"))
        c.execute(sqlalchemy.text("CREATE TABLE _ro_probe (id int)"))
        c.execute(sqlalchemy.text("INSERT INTO _ro_probe VALUES (1), (2)"))
        c.commit()
    e.dispose()

    monkeypatch.setattr(external_db, "assert_read_only", lambda q: None)
    with pytest.raises(Exception) as exc:
        external_db.run_query(url, "DELETE FROM _ro_probe", {}, 10)
    assert "read-only" in str(exc.value).lower()

    monkeypatch.undo()
    assert len(run_query(url, "SELECT * FROM _ro_probe", {}, 10)) == 2
