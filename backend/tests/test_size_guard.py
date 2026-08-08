"""
Refuser plutôt que mourir.

Le plafond en mégaoctets laissait passer 1,6 million de lignes — de quoi tuer
le processus, pas le ralentir. Ces tests fixent les deux moitiés du contrat :
ce qui est refusé, et surtout ce qui doit continuer de passer, parce qu'un
garde-fou trop zélé sur des fichiers de travail normaux se fait désactiver.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.size_guard import TooManyRows, check_csv, count_csv_rows, max_rows

client = TestClient(app)


def _csv(n: int, cols: int = 4) -> bytes:
    tete = ";".join(["SIRET", "NOM", "MONTANT", "DATE"][:cols])
    lignes = [tete] + [f"1234567890123{i%9};Client{i};{i},50;01/02/1990" for i in range(n)]
    return ("\n".join(lignes) + "\n").encode()


# ── Le comptage ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 250, 5_000])
def test_le_comptage_est_juste(n):
    assert count_csv_rows(_csv(n), ";") == n


def test_le_comptage_ne_charge_pas_le_fichier():
    """Le point de DuckDB : compter 200 000 lignes ne doit pas coûter la
    mémoire qu'on cherche justement à ne pas dépenser."""
    import gc
    try:
        import resource          # absent sous Windows — CI (Linux) le couvre
    except ImportError:
        pytest.skip("module 'resource' indisponible sur cette plateforme")
    gros = _csv(200_000)
    gc.collect()
    avant = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    assert count_csv_rows(gros, ";") == 200_000
    apres = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    assert apres - avant < 60, f"le comptage a coûté {apres - avant:.0f} Mo"


@pytest.mark.parametrize("brut", [
    b"\x00\x01\x02 pas du csv du tout",
    b"",
    b"une seule ligne sans separateur",
])
def test_un_fichier_illisible_ne_bloque_pas(brut, monkeypatch):
    """Ne pas savoir compter n'est pas une raison de refuser : ce module est un
    garde-fou volumétrique, pas un validateur. Le parseur du projet dira ce qui
    ne va pas, avec un message qui a du sens.

    Le contrat porte sur le comportement, pas sur la valeur renvoyée : selon
    le fichier, DuckDB rend `None` (il n'a pas su lire) ou `0` (il a lu et n'a
    trouvé aucune ligne exploitable). Les deux doivent laisser passer."""
    monkeypatch.setenv("FX_MAX_ROWS", "10")
    assert check_csv(brut, ";") in (None, 0)          # surtout : ne lève pas


# ── Le plafond ─────────────────────────────────────────────────────────────

def test_au_dessus_du_plafond_c_est_refuse(monkeypatch):
    monkeypatch.setenv("FX_MAX_ROWS", "1000")
    with pytest.raises(TooManyRows) as e:
        check_csv(_csv(1500), ";")
    assert e.value.rows == 1500 and e.value.limit == 1000


def test_le_message_dit_les_deux_nombres_et_quoi_faire(monkeypatch):
    monkeypatch.setenv("FX_MAX_ROWS", "1000")
    with pytest.raises(TooManyRows) as e:
        check_csv(_csv(1500), ";")
    m = str(e.value)
    assert "1\u202f500" in m and "1\u202f000" in m          # espace fine insécable
    assert "Découpez-le, ou passez" in m, "la virgule de la phrase doit survivre"
    assert "flux" in m and "FX_MAX_ROWS" in m


def test_sous_le_plafond_ca_passe(monkeypatch):
    monkeypatch.setenv("FX_MAX_ROWS", "1000")
    assert check_csv(_csv(999), ";") == 999


def test_le_plafond_est_reglable(monkeypatch):
    monkeypatch.setenv("FX_MAX_ROWS", "42")
    assert max_rows() == 42
    monkeypatch.setenv("FX_MAX_ROWS", "pas un nombre")
    assert max_rows() == 500_000                          # repli sur le défaut


# ── Bout à bout, par la vraie route ────────────────────────────────────────

def test_l_upload_refuse_proprement(monkeypatch):
    """413 avec un message lisible, au lieu d'un processus qui disparaît."""
    monkeypatch.setenv("FX_MAX_ROWS", "500")
    r = client.post("/api/files",
                    files={"file": ("gros.csv", io.BytesIO(_csv(2000)), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 413, r.text
    assert "2\u202f000" in r.text and "500" in r.text


def test_l_upload_normal_n_est_pas_gene():
    """La taille de travail habituelle doit passer sans que rien ne change."""
    r = client.post("/api/files",
                    files={"file": ("ok.csv", io.BytesIO(_csv(2000)), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"]
    assert r.json()["preview"]["total_rows"] == 2000
